"""Automated data-quality checks 2-7, run on every refresh (notebook 12). Pure Python, unit-tested.

Plain-language summary: each check returns a result with a name, pass/fail, the measured numbers and, when it fails,
a few examples. The notebook prints all results and then raises an error if any check failed, so a bad refresh stops
loudly instead of feeding wrong numbers to the models and the app. Thresholds are set from the measured values of the
2026-10-21/22 runs with some headroom; changing one is a decision, recorded in docs/02_data_model.md section 2d.

  2  duplicates       unique keys in every table; no contract kept from two datasets; no exact duplicate award rows
  3  joint awards     contract total = sum of supplier shares; STEP rows are shares, not repeated full amounts
  4  standard values  every universe contract has a mapped category and method
  5  columns          required fields filled per dataset; fiscal year agrees with the signing date; datasets stay in
                      their documented fiscal years; the two STEP datasets have the same columns
  6  suppliers        every award has a supplier key; masked placeholders never merged; known answer (GANSU JINLONGYI)
  7  durations        every duration starts at an IFB / REOI notice; none negative or over 3 years; match rate floor
"""
from collections import Counter

from src.entity_resolution import keys
from src.ingest.transform import START_NOTICE_TYPES

DOCUMENTED_FY = {"DS01004": (2001, 2016), "DS04691": (2017, 2019), "DS00005": (2020, 2100)}
REQUIRED = ("project_id", "signing_date", "fiscal_year", "total_amount_usd")
MIN_FILLED = 0.98                 # share of contracts per dataset with each required field (measured ~1.0)
MAX_REPEATED_SHARE = 0.25         # share of decidable STEP joint contracts that look repeated (measured 1/29, 1/17)
MIN_DURATION_MATCH_RATE = 0.25    # matched / contracts, STEP universe (measured 36.0%)
AMOUNT_TOLERANCE = 0.01           # USD


def result(check, name, ok, numbers=None, examples=None):
    return {"check": check, "name": name, "passed": bool(ok), "numbers": numbers or {},
            "examples": (examples or [])[:5]}


# --- 2. duplicates ---------------------------------------------------------------------------------------------------

def duplicate_keys(rows, key, check, name):
    c = Counter(r.get(key) for r in rows)
    dups = [k for k, n in c.items() if n > 1]
    nulls = c.get(None, 0)
    return result(check, name, not dups and not nulls,
                  {"rows": len(rows), "duplicate_keys": len(dups), "null_keys": nulls}, dups)


def cross_dataset(contracts):
    also = [c for c in contracts if c.get("also_in")]
    return result(2, "contract numbers kept from one dataset only (precedence DS00005 > DS04691 > DS01004)", True,
                  {"contracts_also_listed_elsewhere": len(also),
                   "by_combination": dict(Counter("+".join([c["source_dataset"]] + list(c["also_in"])) for c in also))},
                  [c["contract_key"] for c in also])


def duplicate_award_rows(awards):
    key = lambda a: (a["contract_key"], a.get("raw_supplier_name"), a.get("source_supplier_id"),
                     a.get("supplier_country"), a.get("amount_row_usd"))
    c = Counter(key(a) for a in awards)
    dups = [k for k, n in c.items() if n > 1]
    return result(2, "no exact duplicate award rows", not dups, {"award_rows": len(awards), "duplicated": len(dups)},
                  [k[0] for k in dups])


def orphan_awards(contracts, awards):
    ck = {c["contract_key"] for c in contracts}
    orphans = [a["contract_key"] for a in awards if a["contract_key"] not in ck]
    return result(2, "every award belongs to a kept contract", not orphans, {"orphan_award_rows": len(orphans)},
                  orphans)


# --- 3. joint awards -------------------------------------------------------------------------------------------------

def totals_match_shares(contracts, awards):
    s = Counter()
    for a in awards:
        if a.get("amount_usd") is not None:
            s[a["contract_key"]] += a["amount_usd"]
    bad = [c["contract_key"] for c in contracts if c.get("total_amount_usd") is not None
           and abs(c["total_amount_usd"] - s.get(c["contract_key"], 0.0)) > AMOUNT_TOLERANCE]
    by = Counter((c["source_dataset"], c.get("amount_rule")) for c in contracts)
    return result(3, "contract total = sum of supplier shares", not bad,
                  {"mismatches": len(bad), "contracts_by_dataset_and_amount_rule": {f"{d}|{r}": n for (d, r), n in by.items()},
                   "joint_awards_by_dataset": dict(Counter(c["source_dataset"] for c in contracts if c.get("is_joint_award")))},
                  bad)


def joint_award_basis(summary):
    """summary: output of joint_awards.verdicts(...)[1]. Fails if STEP rows look like repeated full amounts."""
    from src.quality.joint_awards import repeated_share
    shares = {ds: repeated_share(v) for ds, v in summary.items()}
    bad = [ds for ds, r in shares.items() if ds in keys.STEP_DATASETS and r is not None and r > MAX_REPEATED_SHARE]
    multi_ds01004 = summary.get("DS01004", {}).get("joint_contracts", 0)
    return result(3, "STEP joint rows hold shares (checked against award-notice prices); DS01004 one row per contract",
                  not bad and multi_ds01004 == 0,
                  {"repeated_share_by_dataset": shares, "summary": summary, "ds01004_multi_row_contracts": multi_ds01004},
                  bad)


# --- 4. standard values ----------------------------------------------------------------------------------------------

def standard_values(contracts):
    uni = [c for c in contracts if c.get("in_universe")]
    unm = [c for c in uni if not c.get("values_mapped")]
    raw = Counter((c.get("procurement_category_raw"), c.get("procurement_method_raw")) for c in unm)
    all_unm = Counter(c.get("procurement_method_raw") for c in contracts if not c.get("values_mapped"))
    return result(4, "every universe contract has a mapped category and method", not unm,
                  {"universe_contracts": len(uni), "universe_unmapped": len(unm),
                   "unmapped_outside_universe_by_method": dict(all_unm.most_common(10)),
                   "universe_by_category": dict(Counter(c["procurement_category"] for c in uni)),
                   "universe_by_method": dict(Counter(c["procurement_method"] for c in uni).most_common())},
                  [f"{k[0]} | {k[1]}" for k, _ in raw.most_common(5)])


# --- 5. columns ------------------------------------------------------------------------------------------------------

def required_fields(contracts):
    out, bad = {}, []
    for ds in keys.CONTRACT_DATASETS:
        cs = [c for c in contracts if c["source_dataset"] == ds]
        if not cs:
            continue
        filled = {f: round(sum(c.get(f) is not None for c in cs) / len(cs), 4) for f in REQUIRED}
        out[ds] = filled
        bad += [f"{ds}.{f}={v}" for f, v in filled.items() if v < MIN_FILLED]
    return result(5, f"required fields filled in >= {MIN_FILLED:.0%} of contracts per dataset", not bad, out, bad)


def fiscal_year_consistency(contracts):
    mism = [c["contract_key"] for c in contracts if c.get("signing_date") and c.get("fiscal_year")
            and keys.fiscal_year(c["signing_date"]) != c["fiscal_year"]]
    out_rng = [c["contract_key"] for c in contracts if c.get("fiscal_year") is not None
               and not DOCUMENTED_FY[c["source_dataset"]][0] <= c["fiscal_year"] <= DOCUMENTED_FY[c["source_dataset"]][1]]
    return result(5, "fiscal year = FY of signing date; each dataset inside its documented years",
                  not mism and not out_rng, {"fy_vs_signing_mismatch": len(mism), "outside_documented_fy": len(out_rng)},
                  mism + out_rng)


def column_sets(columns_by_dataset):
    """columns_by_dataset: {dataset: set of raw field names seen in bronze}."""
    a, b = columns_by_dataset.get("DS00005", set()), columns_by_dataset.get("DS04691", set())
    need01 = {"contract_number", "supplier_contract_amount", "project_id", "contract_signing_date", "fiscal_year",
              "procurement_category", "procurement_method", "supplier", "borrower_contract_reference_number"}
    missing01 = sorted(need01 - columns_by_dataset.get("DS01004", set()))
    return result(5, "DS00005 and DS04691 share one schema; DS01004 has the fields we map", a == b and not missing01,
                  {"n_columns": {k: len(v) for k, v in columns_by_dataset.items()},
                   "step_only_in_DS00005": sorted(a - b), "step_only_in_DS04691": sorted(b - a),
                   "DS01004_missing": missing01})


# --- 6. suppliers ----------------------------------------------------------------------------------------------------

def supplier_resolution(n_awards_without_key, n_masked_merged, ds01004_suppliers, ds01004_with_id, known_answer_ok):
    return result(6, "every award resolved; masked placeholders never merged; GANSU JINLONGYI -> 323849",
                  n_awards_without_key == 0 and n_masked_merged == 0 and known_answer_ok,
                  {"awards_without_supplier_key": n_awards_without_key, "masked_records_in_multi_record_clusters":
                   n_masked_merged, "ds01004_suppliers": ds01004_suppliers, "ds01004_suppliers_matched_to_an_id":
                   ds01004_with_id, "ds01004_match_rate": round(ds01004_with_id / ds01004_suppliers, 3)
                   if ds01004_suppliers else None, "known_answer_gansu_jinlongyi": known_answer_ok})


# --- 7. durations ----------------------------------------------------------------------------------------------------

def durations(duration_rows, notices, duration_report):
    starts = {}
    for n in notices:
        if n.get("tender_key") and n.get("notice_date"):
            starts.setdefault((n["tender_key"], n["notice_date"]), set()).add(n.get("notice_type"))
    not_start = [d["contract_key"] for d in duration_rows
                 if not (starts.get((d["tender_key"], d["start_notice_date"]), set()) & set(START_NOTICE_TYPES))]
    bad_range = [d["contract_key"] for d in duration_rows if not 0 <= d["duration_days"] <= 1095]
    tot = Counter()
    for v in duration_report.values():
        tot.update(v)
    rate = tot["matched"] / tot["contracts"] if tot["contracts"] else None
    return result(7, "durations start at an IFB/REOI notice, stay within 0-1095 days, match rate above floor",
                  not not_start and not bad_range and rate is not None and rate >= MIN_DURATION_MATCH_RATE,
                  {"durations": len(duration_rows), "start_not_ifb_or_reoi": len(not_start), "out_of_range": len(bad_range),
                   "contracts": tot["contracts"], "matched": tot["matched"], "kept": tot["kept"],
                   "match_rate": None if rate is None else round(rate, 3),
                   "kept_rate": round(tot["kept"] / tot["contracts"], 3) if tot["contracts"] else None,
                   "by_review_type": duration_report},
                  not_start + bad_range)


def failures(results):
    return [r for r in results if not r["passed"]]
