# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 12 — Data-quality gate (checks 2–7) and demo-project coverage report
# MAGIC
# MAGIC **What this notebook does:** runs the same data-quality checks on every refresh and **fails loudly** (raises an
# MAGIC error) if any check fails, so the models and the app never run on a broken refresh. It also writes the
# MAGIC per-project coverage report for the 16 demo projects. The check logic is unit-tested in `src/quality/`.
# MAGIC
# MAGIC | # | Check | Fails when |
# MAGIC |---|---|---|
# MAGIC | 2 | Duplicates | a key repeats in contracts / awards / notices / projects; an award has no contract; an exact award row repeats |
# MAGIC | 3 | Joint awards | a contract total ≠ sum of its supplier shares; STEP joint rows look like the full amount repeated (> 25% of contracts checked against award-notice prices); DS01004 has a multi-row contract |
# MAGIC | 4 | Standard values | a universe contract has a category or method the standard map does not know |
# MAGIC | 5 | Columns | a required field is filled in < 98% of a dataset's contracts; fiscal year ≠ FY of the signing date; a dataset leaves its documented years; DS00005 and DS04691 columns differ |
# MAGIC | 6 | Suppliers | an award has no supplier key; a masked placeholder was merged; the known answer (GANSU JINLONGYI, DS01004 contract 1536520 → supplier_id 323849) fails |
# MAGIC | 7 | Durations | a duration does not start at an IFB / REOI notice; a duration is < 0 or > 1,095 days; the match rate falls below 25% |
# MAGIC
# MAGIC **Run after:** 11 (silver) and 30 (supplier resolution). **No API calls.** Results are appended to
# MAGIC `procurement.ops.data_quality_runs`, so every refresh leaves a record.

# COMMAND ----------

import os, sys, json, datetime
from collections import Counter
sys.path.append(os.path.abspath(".."))
import importlib
import src.quality.checks as ck, src.quality.joint_awards as ja, src.quality.project_coverage as pc
import src.quality.standard_values as sv
importlib.reload(sv); importlib.reload(ja); importlib.reload(pc); importlib.reload(ck)

CATALOG = "procurement"
B, S, G, O = (f"{CATALOG}.{x}" for x in ("bronze", "silver", "gold", "ops"))
RUN_TS = datetime.datetime.utcnow().replace(microsecond=0)
results = []

def rows(sql):
    return [r.asDict() for r in spark.sql(sql).collect()]

contracts = rows(f"SELECT * FROM {S}.contracts")
awards = rows(f"SELECT contract_key, raw_supplier_name, supplier_country, source_supplier_id, amount_usd, amount_row_usd "
              f"FROM {S}.contract_awards")
print(f"{len(contracts):,} contracts, {len(awards):,} award rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check 2 — duplicates across and within tables

# COMMAND ----------

results += [
    ck.duplicate_keys(contracts, "contract_key", 2, "contract_key unique in silver.contracts"),
    ck.cross_dataset(contracts),
    ck.duplicate_award_rows(awards),
    ck.orphan_awards(contracts, awards),
    ck.duplicate_keys(rows(f"SELECT project_id FROM {S}.projects"), "project_id", 2, "project_id unique in silver.projects"),
    ck.duplicate_keys(rows(f"SELECT notice_id FROM {S}.notices"), "notice_id", 2, "notice_id unique in silver.notices"),
]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check 3 — joint awards: shares or repeated amounts?
# MAGIC Every multi-supplier contract that has a published Contract Award notice is compared with the notice's signed
# MAGIC price (exchange rate taken from single-supplier contracts of the same project and currency). The per-contract
# MAGIC verdicts go to `ops.joint_award_check` for inspection.

# COMMAND ----------

award_notices = rows(f"SELECT notice_type, tender_key, notice_text FROM {S}.notices "
                     f"WHERE notice_type LIKE '%Contract Award%' AND tender_key IS NOT NULL")
verdict_rows, joint_summary = ja.verdicts(contracts, awards, award_notices)
if verdict_rows:
    spark.createDataFrame([dict(r, row_amounts_usd=[float(a) for a in r["row_amounts_usd"]]) for r in verdict_rows],
                          "contract_key string, source_dataset string, project_id string, n_rows int, "
                          "row_amounts_usd array<double>, notice_currency string, notice_price double, "
                          "sum_over_price double, one_row_over_price double, verdict string") \
         .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{O}.joint_award_check")
results += [ck.totals_match_shares(contracts, awards), ck.joint_award_basis(joint_summary)]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check 4 — standard category / method values
# MAGIC The map itself is written to `ops.standard_value_map` so it can be shown next to the data.

# COMMAND ----------

spark.createDataFrame(sv.mapping_table(), "field string, raw string, standard string, family string, market string") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{O}.standard_value_map")
results.append(ck.standard_values(contracts))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check 5 — column alignment across the three datasets

# COMMAND ----------

TABLES = {"DS00005": "ds00005_contract_awards", "DS04691": "ds04691_contract_awards", "DS01004": "ds01004_contract_awards"}
cols = {ds: {r.k for r in spark.sql(f"SELECT DISTINCT explode(json_object_keys(record_json)) AS k FROM {B}.{t}").collect()}
        for ds, t in TABLES.items()}
results += [ck.required_fields(contracts), ck.fiscal_year_consistency(contracts), ck.column_sets(cols)]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check 6 — supplier resolution (needs notebook 30)
# MAGIC Also measures how many DS01004 suppliers (which carry no supplier ID) were matched to a STEP supplier ID.

# COMMAND ----------

no_key = spark.sql(f"SELECT count(*) AS n FROM {S}.contract_awards_resolved WHERE supplier_key IS NULL").first().n
masked_merged = spark.sql(f"""
  SELECT count(*) AS n FROM {S}.entity_map e
  JOIN (SELECT supplier_key FROM {S}.entity_map GROUP BY supplier_key HAVING count(*) > 1) m USING (supplier_key)
  WHERE e.supplier_name_masked""").first().n
d01 = spark.sql(f"""
  SELECT count(DISTINCT r.supplier_key) AS suppliers,
         count(DISTINCT CASE WHEN size(s.source_supplier_ids) > 0 THEN r.supplier_key END) AS with_id,
         count(*) AS award_rows,
         sum(CASE WHEN size(s.source_supplier_ids) > 0 THEN 1 ELSE 0 END) AS award_rows_with_id
  FROM {S}.contract_awards_resolved r
  JOIN {S}.contracts c ON r.contract_key = c.contract_key
  JOIN {S}.suppliers s ON r.supplier_key = s.supplier_key
  WHERE c.source_dataset = 'DS01004' AND NOT s.is_placeholder""").first()
gansu = spark.sql(f"""
  SELECT r.raw_supplier_name, s.source_supplier_ids FROM {S}.contract_awards_resolved r
  JOIN {S}.suppliers s ON r.supplier_key = s.supplier_key WHERE r.contract_key = 'C:1536520'""").collect()
gansu_ok = bool(gansu) and all("323849" in (g.source_supplier_ids or []) for g in gansu)
print("GANSU JINLONGYI (DS01004 contract 1536520):", [(g.raw_supplier_name, g.source_supplier_ids) for g in gansu])
r6 = ck.supplier_resolution(no_key, masked_merged, d01.suppliers, d01.with_id, gansu_ok)
r6["numbers"].update(ds01004_award_rows=d01.award_rows, ds01004_award_rows_with_id=d01.award_rows_with_id)
results.append(r6)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check 7 — tender durations use IFB / REOI notices only

# COMMAND ----------

dur_rows = rows(f"SELECT contract_key, tender_key, start_notice_date, duration_days FROM {S}.tender_durations")
start_notices = rows(f"SELECT tender_key, notice_date, notice_type FROM {S}.notices WHERE tender_key IS NOT NULL")
dur_report = {r["review_type"]: {k: r[k] for k in ("contracts", "matched", "kept", "dropped_negative", "dropped_over_3y")}
              for r in rows(f"SELECT * FROM {O}.tender_duration_report")}
results.append(ck.durations(dur_rows, start_notices, dur_report))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 — Coverage report for the 16 demo projects (report, not a gate)
# MAGIC Contracts per dataset, measured coverage against the project's award notices, and outlier flags
# MAGIC (`src/quality/project_coverage.py`): low coverage, statistical outlier, too few notices to judge, a full year
# MAGIC with no contracts, contracts signed before approval (usually advance contracting) or long after closing.

# COMMAND ----------

projects = rows(f"""SELECT p.project_id, n.name AS country, p.board_approval_date, p.closing_date
                    FROM {S}.projects p LEFT JOIN {S}.countries n ON n.iso3 = p.country_iso3_list[0]
                    WHERE p.in_slice ORDER BY p.project_id""")
slice_ids = {p["project_id"] for p in projects}
cov = pc.report(projects, [c for c in contracts if c["project_id"] in slice_ids],
                rows(f"SELECT project_id, notice_type, tender_key FROM {S}.notices WHERE project_id IN "
                     f"({','.join(repr(p) for p in sorted(slice_ids))})"))
spark.createDataFrame([dict(r, contracts_by_dataset=json.dumps(r["contracts_by_dataset"]),
                            contracts_by_coverage_status=json.dumps(r["contracts_by_coverage_status"])) for r in cov],
                      "project_id string, country string, board_approval_date date, closing_date date, contracts int, "
                      "contracts_by_dataset string, contracts_by_coverage_status string, total_amount_usd double, "
                      "award_notices int, award_notices_matched int, coverage_pct double, empty_full_years array<int>, "
                      "signed_before_approval int, signed_after_closing int, flags array<string>, outlier_fence double") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.slice_project_coverage")
for r in cov:
    print(f'{r["project_id"]} {(r["country"] or "")[:10]:10s} contracts={r["contracts"]:4d} {r["contracts_by_dataset"]} '
          f'award notices={r["award_notices"]:4d} matched={r["award_notices_matched"]:4d} coverage={r["coverage_pct"]} '
          f'flags={r["flags"]}')

# COMMAND ----------

# MAGIC %md
# MAGIC ## Results: saved, printed, and the notebook FAILS if any check failed

# COMMAND ----------

spark.createDataFrame([{"run_ts": RUN_TS, "check": r["check"], "name": r["name"], "passed": r["passed"],
                        "numbers_json": json.dumps(r["numbers"], default=str),
                        "examples_json": json.dumps(r["examples"], default=str)} for r in results],
                      "run_ts timestamp, check int, name string, passed boolean, numbers_json string, examples_json string") \
     .write.mode("append").saveAsTable(f"{O}.data_quality_runs")
for r in results:
    print(f'[{"PASS" if r["passed"] else "FAIL"}] check {r["check"]}: {r["name"]}\n    {json.dumps(r["numbers"], default=str)[:900]}')
    if not r["passed"]:
        print(f'    examples: {r["examples"]}')
failed = ck.failures(results)
print(f"\nSUMMARY: {len(results) - len(failed)} of {len(results)} checks passed; "
      f"slice projects flagged: {sum(1 for r in cov if set(r['flags']) - {'signed_before_approval', 'few_award_notices'})}")
if failed:
    raise AssertionError("DATA-QUALITY GATE FAILED: " + "; ".join(f'check {r["check"]}: {r["name"]}' for r in failed))
