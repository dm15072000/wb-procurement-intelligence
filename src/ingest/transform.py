"""Bronze -> silver transforms (pure Python, unit-tested).

Plain-language summary: raw API records come in many shapes. These functions turn each raw record into one clean,
typed row for a silver table, following docs/02_data_model.md. Missing values stay missing (None), never 0.
"""
import json
from collections import defaultdict

from src.entity_resolution import keys

ROADS_SECTOR = "Rural and Inter-Urban Roads"


def _sectors(p):
    out = []
    for i in range(1, 6):
        s = p.get(f"sector{i}")
        if isinstance(s, dict) and s.get("Name"):
            try:
                pct = int(s.get("Percent") or 0)
            except (TypeError, ValueError):
                pct = None
            out.append((s["Name"], pct))
    return out


def _amount(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def project_row(p):
    """Projects API record -> silver.projects row. Returns None if the project ID is invalid."""
    pid = keys.project_id(p.get("id"))
    if not pid:
        return None
    sectors = _sectors(p)
    cc = p.get("countrycode")
    cc = cc if isinstance(cc, list) else ([cc] if cc else [])
    approval = keys.parse_date(p.get("boardapprovaldate"))
    closing = keys.parse_date(p.get("closingdate"))
    fy = p.get("approvalfy")
    return {
        "project_id": pid,
        "project_name": p.get("project_name"),
        "country_iso2_list": [c for c in cc if c],
        "is_multi_country": len(cc) != 1,
        "region": p.get("regionname"),
        "approval_fy": int(fy) if str(fy or "").isdigit() else None,
        "board_approval_date": approval,
        "closing_date": closing,
        "status": p.get("status"),
        "sectors_json": json.dumps([{"name": n, "pct": pct} for n, pct in sectors]),
        "roads_pct": sum(pct or 0 for name, pct in sectors if name == ROADS_SECTOR),
        "ida_commitment_usd": _amount(p.get("idacommamt")),
        "ibrd_commitment_usd": _amount(p.get("ibrdcommamt")),
        "total_commitment_usd": _amount(p.get("totalcommamt")),
        "lending_instrument": p.get("lendinginstr"),
        "env_category": p.get("envassesmentcategorycode"),
        "implementing_agency": p.get("impagency"),
    }


def in_universe(row, min_roads_pct=25, fy_from=2014, fy_to=2021):
    """Training universe (CLAUDE.md): road projects (roads >= 25% of the project), approved FY2014-FY2021, any status."""
    return bool(row and row["roads_pct"] >= min_roads_pct and row["approval_fy"]
                and fy_from <= row["approval_fy"] <= fy_to)


def contracts_from_awards(rows, source):
    """DS00005 / DS01004 supplier rows -> (contracts, awards).

    DS00005 lists a joint venture as one row per member with the value split equally (verified), so one contract is
    many rows. Contracts get the summed amount and the supplier count; awards keep one row per supplier.
    """
    step = source in keys.STEP_DATASETS   # DS00005 and DS04691 share the STEP schema (verified)
    num_field = "wb_contract_number" if step else "contract_number"
    amt_field = "supplier_contract_amount_usd" if step else "supplier_contract_amount"
    grouped = defaultdict(list)
    for r in rows:
        ck = keys.contract_key(r.get(num_field))
        if ck:
            grouped[ck].append(r)
    contracts, awards = [], []
    for ck, rs in grouped.items():
        first = rs[0]
        signed = keys.parse_date(first.get("contract_signing_date"))
        amounts = [_amount(r.get(amt_field)) for r in rs]
        contracts.append({
            "contract_key": ck,
            "source_dataset": source,
            "project_id": keys.project_id(first.get("project_id")),
            "borrower_reference": first.get("borrower_contract_reference_number"),
            "ref_key": keys.ref_key(first.get("borrower_contract_reference_number")),
            "description": first.get("contract_description"),
            "signing_date": signed,
            "fiscal_year": first.get("fiscal_year") if isinstance(first.get("fiscal_year"), int) else (keys.fiscal_year(signed) if signed else None),
            "procurement_category": first.get("procurement_category"),
            "procurement_method": first.get("procurement_method"),
            # Null review type is stored as "unknown", never guessed. DS01004 has no review-type field at all, so its
            # contracts are "unknown" too, even though the dataset is described as prior-review only.
            "review_type": (first.get("review_type") or "unknown") if step else "unknown",
            "coverage_status": keys.coverage_status(first.get("fiscal_year") if isinstance(first.get("fiscal_year"), int)
                                                    else (keys.fiscal_year(signed) if signed else None)),
            "borrower_country": first.get("borrower_country"),
            "borrower_country_code": first.get("borrower_country_code"),
            "total_amount_usd": sum(a for a in amounts if a is not None) if any(a is not None for a in amounts) else None,
            "n_suppliers": len(rs),
        })
        for r, a in zip(rs, amounts):
            awards.append({
                "contract_key": ck,
                "raw_supplier_name": r.get("supplier"),
                "supplier_country": r.get("supplier_country"),
                "supplier_country_code": r.get("supplier_country_code"),
                "source_supplier_id": str(r["supplier_id"]) if r.get("supplier_id") not in (None, "") else None,
                "procurement_method": r.get("procurement_method"),
                "amount_usd": a,
            })
    return contracts, awards


def merge_datasets(per_source):
    """De-duplicate contracts across datasets by contract number.

    per_source: {"DS00005": (contracts, awards), "DS04691": (...), "DS01004": (...)}.
    When a contract number appears in several datasets, ONE copy is kept, by precedence DS00005 > DS04691 > DS01004
    (newest, richest snapshot first), and only that copy's awards are kept, so amounts are never double-counted.
    Returns (contracts, awards, report): report counts contracts per combination of datasets, and among overlaps how
    many disagree on project or signing date.
    """
    seen = defaultdict(dict)  # contract_key -> {source: contract}
    for src in keys.CONTRACT_DATASETS:
        for c in per_source.get(src, ([], []))[0]:
            seen[c["contract_key"]][src] = c
    contracts, chosen = [], {}
    combos, conflicts = defaultdict(int), {"project_differs": 0, "signing_date_differs": 0}
    for ck, by_src in seen.items():
        srcs = [s for s in keys.CONTRACT_DATASETS if s in by_src]
        combos["+".join(srcs)] += 1
        keep = by_src[srcs[0]]
        if len(srcs) > 1:
            if len({by_src[s]["project_id"] for s in srcs}) > 1:
                conflicts["project_differs"] += 1
            if len({by_src[s]["signing_date"] for s in srcs}) > 1:
                conflicts["signing_date_differs"] += 1
        contracts.append(dict(keep, also_in=srcs[1:]))
        chosen[ck] = srcs[0]
    awards = [a for src in keys.CONTRACT_DATASETS for a in per_source.get(src, ([], []))[1]
              if chosen.get(a["contract_key"]) == src]
    report = {"contracts_by_dataset_combination": dict(combos), "overlap_conflicts": conflicts,
              "contracts_after_dedup": len(contracts),
              "contracts_before_dedup": sum(len(v[0]) for v in per_source.values())}
    return contracts, awards, report


def notice_row(n):
    """procnotices record -> silver.notices row."""
    from datetime import datetime
    try:
        nd = datetime.strptime((n.get("noticedate") or "")[:11], "%d-%b-%Y").date()
    except ValueError:
        nd = None
    pid = keys.project_id(n.get("project_id"))
    return {
        "notice_id": n.get("id"),
        "project_id": pid,
        "notice_type": n.get("notice_type"),
        "notice_date": nd,
        "notice_status": n.get("notice_status"),
        "bid_reference_no": n.get("bid_reference_no"),
        "tender_key": keys.tender_key(pid, n.get("bid_reference_no")),
        "bid_description": n.get("bid_description"),
        "procurement_group": n.get("procurement_group"),
        "procurement_method_code": n.get("procurement_method_code"),
        "notice_text": n.get("notice_text"),
    }


START_NOTICE_TYPES = ("Invitation for Bids", "Request for Expression of Interest")


def tender_durations(contracts, notices, max_days=1095):
    """Tender duration per contract (CLAUDE.md data rule): signing date - earliest matching IFB/REOI notice date, joined
    on project ID + normalized reference. Negative or > 3-year durations are dropped as data errors and COUNTED.

    Returns (rows, report): rows for silver.tender_durations; report with match and drop counts by review type.
    """
    starts = defaultdict(list)
    for n in notices:
        if n["notice_type"] in START_NOTICE_TYPES and n["tender_key"] and n["notice_date"]:
            starts[n["tender_key"]].append(n["notice_date"])
    rows = []
    report = defaultdict(lambda: {"contracts": 0, "matched": 0, "kept": 0, "dropped_negative": 0, "dropped_over_3y": 0})
    for c in contracts:
        rt = c["review_type"] or "unknown"
        report[rt]["contracts"] += 1
        tk = f"{c['project_id']}:{c['ref_key']}" if c["project_id"] and c["ref_key"] else None
        if not (tk and tk in starts and c["signing_date"]):
            continue
        report[rt]["matched"] += 1
        start = min(starts[tk])
        days = (c["signing_date"] - start).days
        if days < 0:
            report[rt]["dropped_negative"] += 1
            continue
        if days > max_days:
            report[rt]["dropped_over_3y"] += 1
            continue
        report[rt]["kept"] += 1
        rows.append({"contract_key": c["contract_key"], "tender_key": tk, "start_notice_date": start,
                     "signing_date": c["signing_date"], "duration_days": days, "review_type": c["review_type"]})
    return rows, {k: dict(v) for k, v in report.items()}


def notice_coverage(contracts, notices, group_keys=()):
    """Measured coverage of the contract data, from an INDEPENDENT source: of the 'Contract Award' notices published in
    a fiscal year, what share match a contract in our data (project ID + normalized reference)?

    Returns {(group..., fiscal_year): {"award_notices", "matched", "coverage_pct"}}. coverage_pct is None when there
    were no award notices (unknown, not 0%). It covers only contracts that got a published award notice (mostly larger
    / prior-review ones), so it is an indicator of completeness, not an exact census.
    """
    have = {f"{c['project_id']}:{c['ref_key']}" for c in contracts if c.get("project_id") and c.get("ref_key")}
    out = defaultdict(lambda: {"award_notices": 0, "matched": 0})
    for n in notices:
        if n.get("notice_type") != "Contract Award" or not n.get("tender_key") or not n.get("notice_date"):
            continue
        g = tuple(n.get(k) for k in group_keys) + (keys.fiscal_year(n["notice_date"]),)
        out[g]["award_notices"] += 1
        out[g]["matched"] += n["tender_key"] in have
    for v in out.values():
        v["coverage_pct"] = round(v["matched"] / v["award_notices"], 3) if v["award_notices"] else None
    return dict(out)


def fy_activity(contracts, notices, fy_from, fy_to, group_keys=("project_id",)):
    """Contract counts and value per group and fiscal year, with a coverage status and a MEASURED coverage figure.

    FY2017-19 are no longer NULL (DS04691 covers them, decision 2026-10-21) but flagged partial; FY2017 is flagged
    likely incomplete (STEP rollout). Zero contracts in a year means "none recorded", and must be read together with
    coverage_status and notice_coverage_pct, never as "no activity" on its own.
    """
    agg = defaultdict(lambda: [0, 0.0])
    groups = set()
    for c in contracts:
        g = tuple(c.get(k) for k in group_keys)
        groups.add(g)
        fy = c.get("fiscal_year")
        if fy is not None:
            agg[(g, fy)][0] += 1
            agg[(g, fy)][1] += c.get("total_amount_usd") or 0.0
    cov = notice_coverage(contracts, notices, group_keys)
    out = []
    for g in sorted(groups, key=str):
        for fy in range(fy_from, fy_to + 1):
            n, amt = agg.get((g, fy), (0, 0.0))
            nc = cov.get(g + (fy,), {"award_notices": 0, "matched": 0, "coverage_pct": None})
            row = dict(zip(group_keys, g))
            row.update({"fiscal_year": fy, "coverage_status": keys.coverage_status(fy),
                        "n_contracts": n, "amount_usd": amt, "award_notices": nc["award_notices"],
                        "award_notices_matched": nc["matched"], "notice_coverage_pct": nc["coverage_pct"]})
            out.append(row)
    return out


def governance_rows(api_page):
    """Indicators API page [meta, rows] -> silver.governance rows (value None = missing, flagged)."""
    rows = api_page[1] if isinstance(api_page, list) and len(api_page) > 1 and api_page[1] else []
    out = []
    for x in rows:
        iso3 = x.get("countryiso3code")
        if not iso3 or not str(x.get("date", "")).isdigit():
            continue
        out.append({"iso3": iso3, "year": int(x["date"]), "indicator": x["indicator"]["id"],
                    "value": x.get("value"), "is_missing": x.get("value") is None})
    return out


def country_rows(api_page):
    """Country API page -> silver.countries rows (aggregates excluded)."""
    rows = api_page[1] if isinstance(api_page, list) and len(api_page) > 1 else []
    out = []
    for x in rows:
        lt = (x.get("lendingType") or {}).get("value")
        if (x.get("region") or {}).get("value") == "Aggregates":
            continue
        out.append({"iso3": x["id"], "iso2": x.get("iso2Code"), "name": x.get("name"),
                    "region": (x.get("region") or {}).get("value"), "income_level": (x.get("incomeLevel") or {}).get("value"),
                    "lending_type": {"IDA": "IDA", "IBRD": "IBRD", "Blend": "Blend"}.get(lt, lt)})
    return out
