# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 11 — Bronze → silver: clean, typed structured tables (Stage 3)
# MAGIC
# MAGIC **What this notebook does:** turns raw bronze records into the clean silver tables defined in
# MAGIC `docs/02_data_model.md`, using the unit-tested transforms in `src/ingest/transform.py`. It then runs data-quality
# MAGIC checks and prints a summary for checkpoint (c).
# MAGIC
# MAGIC **Rules applied here (CLAUDE.md):**
# MAGIC - missing values stay NULL, never 0;
# MAGIC - **three contract datasets**, de-duplicated by contract number (precedence DS00005 > DS04691 > DS01004), with
# MAGIC   overlap counts reported; null review type stored as `"unknown"`, never guessed;
# MAGIC - **coverage by fiscal year**: FY2020+ `full` (DS00005), FY2018–19 `partial` and FY2017
# MAGIC   `partial_likely_incomplete` (DS04691, STEP rollout), FY≤2016 `prior_review_only` (DS01004). Coverage is
# MAGIC   **measured** against published Contract Award notices, per FY and per project;
# MAGIC - tender duration = signing − earliest matching IFB/REOI notice; negative and > 3-year values are **dropped and
# MAGIC   counted**; results are reported separately for prior and post review;
# MAGIC - contract models use the **STEP datasets DS00005 + DS04691**; DS01004 is kept for descriptive history.
# MAGIC
# MAGIC Runs entirely from bronze: **no API calls**.

# COMMAND ----------

import os, sys, json
from collections import Counter
sys.path.append(os.path.abspath(".."))
import importlib
import src.ingest.transform as tf
import src.entity_resolution.keys as keys
importlib.reload(tf); importlib.reload(keys)

CATALOG = "procurement"
B, S, G, O = (f"{CATALOG}.{x}" for x in ("bronze", "silver", "gold", "ops"))

# The 16 demo-slice projects chosen at checkpoint (b) (CLAUDE.md / docs/04_data_sources.md).
SLICE = ["P147864", "P163239", "P157054", "P160505", "P155522", "P151449", "P153548", "P132833",
         "P144726", "P143751", "P132775", "P132621", "P132982", "P145778", "P129401", "P121185"]

dup_report = {}

def records(table):
    """Bronze records with EXACT duplicates removed (and counted). Paging through a source that changes during the
    download (DS00005 updates daily) can return the same record on two pages. Stage 2 saw 22,729 project rows but only
    22,589 distinct project IDs. A duplicated supplier row would double a contract's amount, so this matters."""
    raw = [r.record_json for r in spark.table(f"{B}.{table}").select("record_json").collect()]
    uniq = list(dict.fromkeys(raw))  # keeps first occurrence, preserves order
    dup_report[table] = {"rows": len(raw), "exact_duplicates_removed": len(raw) - len(uniq)}
    return [json.loads(x) for x in uniq]

def save(rows, table, ddl):
    """Write Python dicts to a Delta table with an explicit schema (no inference surprises)."""
    df = spark.createDataFrame(rows, schema=ddl)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    print(f"{table}: {df.count():,} rows")

report = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Countries and projects
# MAGIC Projects get their iso3 country through the crosswalk (contract and project country codes are World Bank codes,
# MAGIC which can differ from ISO). Flags: `in_universe` (training) and `in_slice` (demo, documents).

# COMMAND ----------

countries = tf.country_rows([{}, records("countries")])
save(countries, f"{S}.countries", "iso3 string, iso2 string, name string, region string, income_level string, lending_type string")
iso2_to_iso3 = {c["iso2"]: c["iso3"] for c in countries if c["iso2"]}

# One row per project_id: a project seen on two pages may differ slightly (updated between pages); keep the last copy.
projects = list({p["project_id"]: p for p in (tf.project_row(r) for r in records("projects")) if p}.values())
dup_report["projects"]["distinct_project_ids"] = len(projects)
for p in projects:
    p["country_iso3_list"] = [iso2_to_iso3.get(c) for c in p["country_iso2_list"] if iso2_to_iso3.get(c)]
    p["in_universe"] = tf.in_universe(p)
    p["in_slice"] = p["project_id"] in SLICE
save(projects, f"{S}.projects",
     "project_id string, project_name string, country_iso2_list array<string>, is_multi_country boolean, region string, "
     "approval_fy int, board_approval_date date, closing_date date, status string, sectors_json string, roads_pct int, "
     "ida_commitment_usd double, ibrd_commitment_usd double, total_commitment_usd double, lending_instrument string, "
     "env_category string, implementing_agency string, country_iso3_list array<string>, in_universe boolean, in_slice boolean")
UNIVERSE = {p["project_id"] for p in projects if p["in_universe"]}
missing_slice = sorted(set(SLICE) - UNIVERSE)
report["projects"] = {"all": len(projects), "universe": len(UNIVERSE), "slice": len(SLICE),
                      "slice_not_in_universe": missing_slice}
assert not missing_slice, f"slice projects outside the universe rule: {missing_slice}"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Contracts and awards: DS00005 + DS04691 + DS01004, de-duplicated
# MAGIC One contract per contract number, with joint-venture member rows summed. The contract key is the number alone, so
# MAGIC a contract listed in two datasets collapses to one. The kept copy follows precedence DS00005 > DS04691 > DS01004,
# MAGIC and only its awards are kept, so amounts are never double-counted. Contracts are kept for **all** projects; the
# MAGIC universe flag filters later.

# COMMAND ----------

SOURCES = (("DS00005", "ds00005_contract_awards"), ("DS04691", "ds04691_contract_awards"),
           ("DS01004", "ds01004_contract_awards"))
per_source = {ds: tf.contracts_from_awards(records(table), ds) for ds, table in SOURCES}
contracts, awards, merge_report = tf.merge_datasets(per_source)
for c in contracts:
    c["in_universe"] = c["project_id"] in UNIVERSE
save(contracts, f"{S}.contracts",
     "contract_key string, source_dataset string, project_id string, borrower_reference string, ref_key string, "
     "description string, signing_date date, fiscal_year int, procurement_category string, procurement_method string, "
     "review_type string, coverage_status string, borrower_country string, borrower_country_code string, "
     "total_amount_usd double, n_suppliers int, also_in array<string>, in_universe boolean")
save(awards, f"{S}.contract_awards",
     "contract_key string, raw_supplier_name string, supplier_country string, supplier_country_code string, "
     "source_supplier_id string, procurement_method string, amount_usd double")

# Sanity check (replaces the old "no contracts in FY2017-19" assertion): each dataset must stay inside the fiscal years
# its own description documents. A violation means the coverage rules need revisiting, not silent acceptance.
DOCUMENTED_FY = {"DS01004": (2001, 2016), "DS04691": (2017, 2019), "DS00005": (2020, 2100)}
out_of_range = {ds: dict(Counter(c["fiscal_year"] for c in per_source[ds][0]
                                 if c["fiscal_year"] is not None
                                 and not DOCUMENTED_FY[ds][0] <= c["fiscal_year"] <= DOCUMENTED_FY[ds][1]))
                for ds in DOCUMENTED_FY}
report["contracts"] = {
    "per_dataset_before_dedup": {ds: len(per_source[ds][0]) for ds in per_source},
    "dedup": merge_report,
    "fiscal_years_outside_documented_range": out_of_range,
    "review_type_by_dataset": {ds: dict(Counter(c["review_type"] for c in per_source[ds][0])) for ds in per_source},
    "kept_contracts_by_coverage_status": dict(Counter(c["coverage_status"] for c in contracts)),
    "universe_contracts_by_dataset": dict(Counter(c["source_dataset"] for c in contracts if c["in_universe"])),
}
assert not any(out_of_range.values()), f"dataset rows outside their documented fiscal years: {out_of_range}"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Procurement notices and tender durations (H1 rule)
# MAGIC Duration = signing − earliest matching Invitation for Bids / REOI, joined on project + normalized reference.
# MAGIC The drop report (negative, over 3 years) is stored in `ops.tender_duration_report`, so it can be quoted.

# COMMAND ----------

notices = [tf.notice_row(r) for r in records("procurement_notices")]
save(notices, f"{S}.notices",
     "notice_id string, project_id string, notice_type string, notice_date date, notice_status string, "
     "bid_reference_no string, tender_key string, bid_description string, procurement_group string, "
     "procurement_method_code string, notice_text string")

# Models train on the STEP datasets (DS00005 + DS04691), universe projects only (CLAUDE.md data rule).
uni_step = [c for c in contracts if c["in_universe"] and c["source_dataset"] in keys.STEP_DATASETS]
durations, dur_report = tf.tender_durations(uni_step, notices)
source_of = {c["contract_key"]: c["source_dataset"] for c in uni_step}
for d in durations:
    d["source_dataset"] = source_of[d["contract_key"]]
save(durations, f"{S}.tender_durations",
     "contract_key string, tender_key string, start_notice_date date, signing_date date, duration_days int, "
     "review_type string, source_dataset string")
save([{"review_type": k, **v} for k, v in dur_report.items()], f"{O}.tender_duration_report",
     "review_type string, contracts int, matched int, kept int, dropped_negative int, dropped_over_3y int")
report["tender_durations"] = dur_report
# Same report per dataset, so the contribution of DS04691 (FY2017-19) is visible on its own.
report["tender_durations_by_dataset"] = {
    ds: tf.tender_durations([c for c in uni_step if c["source_dataset"] == ds], notices)[1] for ds in keys.STEP_DATASETS}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Coverage by fiscal year (measured) and contract activity per universe project
# MAGIC **Measured coverage** = of the *Contract Award* notices published in a fiscal year (an independent source), the
# MAGIC share whose reference matches a contract in our data. Award notices exist mainly for larger / prior-review
# MAGIC contracts, so this is a completeness *indicator*, not a census. `notice_coverage_pct` is NULL when a year had no
# MAGIC award notices (unknown, not 0%).
# MAGIC Every (project, FY) from FY2014 to FY2027 gets a row with `coverage_status` and the measured figure. A zero must
# MAGIC always be read together with them: a zero in FY2017 means "none recorded in a likely incomplete year".

# COMMAND ----------

uni_contracts = [c for c in contracts if c["in_universe"]]
activity = tf.fy_activity(uni_contracts, notices, 2014, 2027)
save(activity, f"{G}.contract_activity_fy",
     "project_id string, fiscal_year int, coverage_status string, n_contracts int, amount_usd double, "
     "award_notices int, award_notices_matched int, notice_coverage_pct double")

by_fy = tf.notice_coverage(uni_contracts, notices)            # {(fy,): {...}} across all universe projects
n_by_fy = Counter(c["fiscal_year"] for c in uni_contracts)
coverage_fy = [{"fiscal_year": fy, "coverage_status": keys.coverage_status(fy), "n_contracts": n_by_fy.get(fy, 0),
                **by_fy.get((fy,), {"award_notices": 0, "matched": 0, "coverage_pct": None})}
               for fy in range(2014, 2028)]
save(coverage_fy, f"{G}.contract_coverage_fy",
     "fiscal_year int, coverage_status string, n_contracts int, award_notices int, matched int, coverage_pct double")
report["coverage_by_fy"] = {r["fiscal_year"]: {k: r[k] for k in ("coverage_status", "n_contracts", "award_notices", "coverage_pct")}
                            for r in coverage_fy}
# Per project: how many universe projects have a measurable coverage figure in each partial year, and its median.
for fy in (2017, 2018, 2019):
    vals = sorted(r["notice_coverage_pct"] for r in activity if r["fiscal_year"] == fy and r["notice_coverage_pct"] is not None)
    report.setdefault("per_project_coverage_partial_years", {})[fy] = {
        "projects_with_award_notices": len(vals), "median_coverage": vals[len(vals) // 2] if vals else None}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Governance (CPIA, WGI) and disbursements

# COMMAND ----------

gov = tf.governance_rows([{}, records("governance")])
save(gov, f"{S}.governance", "iso3 string, year int, indicator string, value double, is_missing boolean")
report["governance_missing_share"] = {ind: round(sum(g["is_missing"] for g in gov if g["indicator"] == ind) /
                                      max(1, sum(1 for g in gov if g["indicator"] == ind)), 3)
                                      for ind in sorted({g["indicator"] for g in gov})}

def disb_row(r):
    ida = r.get("_dataset") == "DS00976"
    num = lambda k: r.get(k + ("_us_" if ida else ""))
    return {"dataset": r.get("_dataset"), "loan_or_credit_no": r.get("credit_number") if ida else r.get("loan_number"),
            "project_id": keys.project_id(r.get("project_id")), "end_of_period": keys.parse_date(r.get("end_of_period")),
            "status": r.get("credit_status") if ida else r.get("loan_status"),
            "original_principal_usd": num("original_principal_amount"), "disbursed_usd": num("disbursed_amount"),
            "undisbursed_usd": num("undisbursed_amount"), "cancelled_usd": num("cancelled_amount")}

disb = [disb_row(r) for r in records("disbursements")]
save(disb, f"{S}.disbursements",
     "dataset string, loan_or_credit_no string, project_id string, end_of_period date, status string, "
     "original_principal_usd double, disbursed_usd double, undisbursed_usd double, cancelled_usd double")
report["disbursements"] = {"rows": len(disb), "projects": len({d["project_id"] for d in disb}),
                           "unparsed_dates": sum(d["end_of_period"] is None for d in disb)}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Summary: copy back for checkpoint (c)

# COMMAND ----------

report["duplicates"] = dup_report
print(json.dumps(report, indent=2, default=str))
