# Databricks notebook source
# MAGIC %md
# MAGIC # 11 — Bronze → silver: clean, typed structured tables (Stage 3)
# MAGIC
# MAGIC **What this notebook does:** turns raw bronze records into the clean silver tables defined in
# MAGIC `docs/02_data_model.md`, using the unit-tested transforms in `src/ingest/transform.py`. It then runs data-quality
# MAGIC checks and prints a summary for checkpoint (c).
# MAGIC
# MAGIC **Rules applied here (CLAUDE.md):**
# MAGIC - missing values stay NULL, never 0; the **FY2017–FY2019 contract gap** is NULL + `coverage_gap = true`;
# MAGIC - tender duration = signing − earliest matching IFB/REOI notice; negative and > 3-year values are **dropped and
# MAGIC   counted**; results are reported separately for prior and post review;
# MAGIC - contract models will use **DS00005 only**; DS01004 is kept for descriptive history.
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

def records(table):
    return [json.loads(r.record_json) for r in spark.table(f"{B}.{table}").select("record_json").collect()]

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

projects = [p for p in (tf.project_row(r) for r in records("projects")) if p]
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
# MAGIC ## 2. Contracts and awards (DS00005 + DS01004)
# MAGIC One contract per contract number, with joint-venture member rows summed. Awards keep one row per supplier (input
# MAGIC to supplier resolution in notebook 30). Contracts are kept for **all** projects; the universe flag filters later.

# COMMAND ----------

c5, a5 = tf.contracts_from_awards(records("ds00005_contract_awards"), "DS00005")
c4, a4 = tf.contracts_from_awards(records("ds01004_contract_awards"), "DS01004")
contracts = c5 + c4
for c in contracts:
    c["in_universe"] = c["project_id"] in UNIVERSE
save(contracts, f"{S}.contracts",
     "contract_key string, source_dataset string, project_id string, borrower_reference string, ref_key string, "
     "description string, signing_date date, fiscal_year int, procurement_category string, procurement_method string, "
     "review_type string, borrower_country string, borrower_country_code string, total_amount_usd double, "
     "n_suppliers int, in_universe boolean")
save(a5 + a4, f"{S}.contract_awards",
     "contract_key string, raw_supplier_name string, supplier_country string, supplier_country_code string, "
     "source_supplier_id string, procurement_method string, amount_usd double")

# Gap check: both datasets must have NO contracts in FY2017-FY2019 (verified in Stage 2). If this ever fails, the
# gap rule must be revisited, not silently kept.
gap_rows = Counter((c["source_dataset"], c["fiscal_year"]) for c in contracts if keys.in_contract_gap(c["fiscal_year"]))
report["contracts"] = {"DS00005": len(c5), "DS01004": len(c4), "universe_DS00005": sum(c["in_universe"] for c in c5),
                       "rows_in_FY2017_2019": dict((f"{k[0]}:{k[1]}", v) for k, v in gap_rows.items()),
                       "review_type_DS00005": dict(Counter(c["review_type"] for c in c5))}
assert not gap_rows, f"contracts found inside the FY2017-19 gap: {gap_rows}"

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

uni_c5 = [c for c in c5 if c["in_universe"]]   # models use DS00005 only (CLAUDE.md data rule)
durations, dur_report = tf.tender_durations(uni_c5, notices)
save(durations, f"{S}.tender_durations",
     "contract_key string, tender_key string, start_notice_date date, signing_date date, duration_days int, review_type string")
save([{"review_type": k, **v} for k, v in dur_report.items()], f"{O}.tender_duration_report",
     "review_type string, contracts int, matched int, kept int, dropped_negative int, dropped_over_3y int")
report["tender_durations"] = dur_report

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Fiscal-year contract activity per universe project, with the gap as NULL
# MAGIC Every (project, FY) from FY2014 to FY2027 gets a row. FY2017–19: `n_contracts` and `amount_usd` are NULL with
# MAGIC `coverage_gap = true`, so no chart or feature can read the gap as "no activity".

# COMMAND ----------

activity = tf.fy_activity([c for c in contracts if c["in_universe"]], 2014, 2027)
save(activity, f"{G}.contract_activity_fy",
     "project_id string, fiscal_year int, coverage_gap boolean, n_contracts int, amount_usd double")
report["activity_gap_rows_null"] = all(r["n_contracts"] is None for r in activity if r["coverage_gap"])
assert report["activity_gap_rows_null"]

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

print(json.dumps(report, indent=2, default=str))
