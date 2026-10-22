# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 10 — Ingest structured sources → bronze (Stage 3)
# MAGIC
# MAGIC **What this notebook does:** downloads every structured World Bank source verified in Stage 2 and stores it
# MAGIC unchanged: first as raw JSON Lines files in the volume, then as bronze Delta tables.
# MAGIC
# MAGIC **Why raw first:** bronze is our copy of exactly what the source said, on the day we asked. If a later step has a
# MAGIC bug, we re-run it from bronze without calling the APIs again.
# MAGIC
# MAGIC **Resumable:** each page is one file. If the notebook stops (timeout, rate limit), re-running it skips pages that
# MAGIC already exist. To force a fresh download, change `RUN_TAG`.
# MAGIC
# MAGIC **Polite:** every call goes through `src/ingest/http.py`: explicit User-Agent, pause after every call,
# MAGIC exponential backoff honouring HTTP 429 `Retry-After` (CLAUDE.md rule).
# MAGIC
# MAGIC **Expected run time:** roughly 20–40 minutes, mostly DS00005 (~296 pages) and DS01004 (~147 pages).

# COMMAND ----------

# Make the repo's src/ importable (this notebook lives in notebooks/, the repo root is one level up).
import os, sys, json, re
from datetime import datetime, timezone
sys.path.append(os.path.abspath(".."))
import importlib
import src.ingest.http as http
import src.ingest.transform as tf
importlib.reload(http); importlib.reload(tf)

CATALOG = "procurement"
# One folder per ingestion run, so re-runs resume instead of mixing data. Default = the checkpoint (c) run, so
# running by hand reproduces it; the weekly refresh job passes its own date, so each refresh downloads fresh data.
dbutils.widgets.text("run_tag", "2026-10-21")
RUN_TAG = dbutils.widgets.get("run_tag").strip() or "2026-10-21"
RAW = f"/Volumes/{CATALOG}/bronze/raw_files/structured/{RUN_TAG}"

# Create all six schemas from CLAUDE.md (catalog + bronze + volume already exist, verified by notebook 01).
for s in ["bronze", "silver", "gold", "docs", "ml", "ops"]:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{s}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.bronze.raw_files")
os.makedirs(RAW, exist_ok=True)
print("raw files ->", RAW)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helpers: write one page as JSON Lines; register a bronze table
# MAGIC Bronze tables keep each record as its **raw JSON string** (`record_json`) plus where it came from. Some sources
# MAGIC change a field's type between records (e.g. `sector1` is sometimes an object, sometimes ""), which would break
# MAGIC automatic schema inference. Parsing happens in silver, in tested code.

# COMMAND ----------

def page_path(source, n):
    return f"{RAW}/{source}/page_{n:05d}.jsonl"

def write_page(source, n, records):
    os.makedirs(f"{RAW}/{source}", exist_ok=True)
    tmp = page_path(source, n) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, page_path(source, n))  # atomic: a half-written page never looks complete

def done(source, n):
    return os.path.exists(page_path(source, n))

def register_bronze(source):
    """bronze.<source>(record_json STRING, source_file STRING, source STRING, ingested_at TIMESTAMP)"""
    from pyspark.sql import functions as F
    df = (spark.read.text(f"{RAW}/{source}/*.jsonl")
          .select(F.col("value").alias("record_json"), F.col("_metadata.file_path").alias("source_file"))
          .withColumn("source", F.lit(source)).withColumn("ingested_at", F.lit(datetime.now(timezone.utc))))
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.bronze.{source}")
    n = spark.table(f"{CATALOG}.bronze.{source}").count()
    print(f"bronze.{source}: {n:,} records")
    return n

def ingest_paged(source, pages_iter_factory, start_page=0):
    """Pull pages until the source is exhausted, skipping pages already on disk. `pages_iter_factory(skip_pages)`
    must return an iterator of pages starting at that page index."""
    n = start_page
    while done(source, n):
        n += 1
    if n and not os.path.exists(f"{RAW}/{source}/_COMPLETE"):
        print(f"{source}: resuming at page {n}")
    if not os.path.exists(f"{RAW}/{source}/_COMPLETE"):
        for batch in pages_iter_factory(n):
            write_page(source, n, batch)
            n += 1
        open(f"{RAW}/{source}/_COMPLETE", "w").close()
    return register_bronze(source)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Countries (lending type IDA / IBRD / Blend, iso2 ↔ iso3 crosswalk)

# COMMAND ----------

if not os.path.exists(f"{RAW}/countries/_COMPLETE"):
    page = http.get("https://api.worldbank.org/v2/country?format=json&per_page=400")
    write_page("countries", 0, page[1])
    open(f"{RAW}/countries/_COMPLETE", "w").close()
register_bronze("countries")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Projects (all ~22,700, with sector shares)
# MAGIC Stage 2 showed the API ignores `sectorcode_exact`, so we take all projects and filter in silver.

# COMMAND ----------

PROJECT_FL = ("id,project_name,countryshortname,countrycode,regionname,approvalfy,boardapprovaldate,closingdate,status,"
              "sector1,sector2,sector3,sector4,sector5,sector_namecode,idacommamt,ibrdcommamt,totalcommamt,"
              "lendinginstr,prodline,envassesmentcategorycode,impagency")

def projects_from(page_idx):
    import urllib.parse
    os_ = page_idx * 500
    while True:
        d = http.get("https://search.worldbank.org/api/v2/projects?" +
                     urllib.parse.urlencode({"format": "json", "rows": 500, "os": os_, "fl": PROJECT_FL}, safe=","))
        batch = list((d.get("projects") or {}).values())
        yield batch
        if len(batch) < 500:
            return
        os_ += 500

ingest_paged("projects", projects_from)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Contract awards: DS00005 (FY2020+), DS04691 (FY2017–FY2019), DS01004 (FY2001–FY2016, prior review only)
# MAGIC Full datasets (all small enough), so bronze is a faithful copy. Silver keeps what the models need.
# MAGIC DS04691 was found and verified on 2026-10-21 (63,912 rows, same STEP schema as DS00005) and added by decision
# MAGIC (Option A). On a re-run, the sources already downloaded are skipped; only DS04691 is fetched.

# COMMAND ----------

def fone_from(dataset):
    def factory(page_idx):
        skip = page_idx * 1000
        resource = http.FONE_RESOURCES[dataset]
        while True:
            url = f"{http.FONE}?datasetId={dataset}&resourceId={resource}&type=json&top=1000&skip={skip}"
            batch = http.get(url).get("data") or []
            yield batch
            if len(batch) < 1000:
                return
            skip += 1000
    return factory

ingest_paged("ds00005_contract_awards", fone_from("DS00005"))
ingest_paged("ds04691_contract_awards", fone_from("DS04691"))
ingest_paged("ds01004_contract_awards", fone_from("DS01004"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Governance indicators: CPIA and WGI, all countries, 2005–2025
# MAGIC Codes verified in Stage 2. The old WGI codes (`RQ.EST`) no longer exist; the current ones are `GOV_WGI_*`
# MAGIC (source 3).

# COMMAND ----------

INDICATORS = [("IQ.CPA.PUBS.XQ", 2), ("IQ.CPA.TRAN.XQ", 2),
              ("GOV_WGI_RQ.EST", 3), ("GOV_WGI_GE.EST", 3), ("GOV_WGI_RL.EST", 3), ("GOV_WGI_CC.EST", 3)]
if not os.path.exists(f"{RAW}/governance/_COMPLETE"):
    for i, (code, src_id) in enumerate(INDICATORS):
        if done("governance", i):
            continue
        page = http.get(f"https://api.worldbank.org/v2/country/all/indicator/{code}?source={src_id}"
                        f"&format=json&per_page=20000&date=2005:2025")
        write_page("governance", i, page[1] if len(page) > 1 and page[1] else [])
    open(f"{RAW}/governance/_COMPLETE", "w").close()
register_bronze("governance")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Training universe → notices and disbursements for those projects only
# MAGIC **Universe** (CLAUDE.md): road projects (roads ≥ 25% of the project), approved FY2014–FY2021, any status, any
# MAGIC country. Models train on these; documents are parsed only for the 16 demo-slice projects (notebook 20).

# COMMAND ----------

proj_rows = [tf.project_row(json.loads(r.record_json)) for r in spark.table(f"{CATALOG}.bronze.projects").collect()]
UNIVERSE = sorted(r["project_id"] for r in proj_rows if tf.in_universe(r))
print("universe projects:", len(UNIVERSE))

def per_project(source, fetch):
    """One page per project (page index = position in UNIVERSE), resumable."""
    if not os.path.exists(f"{RAW}/{source}/_COMPLETE"):
        for i, pid in enumerate(UNIVERSE):
            if not done(source, i):
                write_page(source, i, fetch(pid))
        open(f"{RAW}/{source}/_COMPLETE", "w").close()
    return register_bronze(source)

NOTICE_FL = ("id,notice_type,noticedate,notice_status,project_id,bid_reference_no,bid_description,procurement_group,"
             "procurement_method_code,submission_deadline_date,notice_text")

def notices_for(pid):
    out = []
    for batch in http.search_pages("procnotices", {"project_id": pid, "fl": NOTICE_FL}, list_key="procnotices", pause=0.7):
        out += batch
    return out

per_project("procurement_notices", notices_for)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Disbursements (IBRD DS00975, IDA DS00976): monthly snapshots per loan / credit
# MAGIC Their API resource IDs were not verified in Stage 2, so we read them from the official API Explorer page at run
# MAGIC time (the same way DS01004's `RS00934` was found).

# COMMAND ----------

def fone_resource(dataset):
    html = http.get(f"https://financesone.worldbank.org/api-explorer?id={dataset}", as_json=False).decode("utf-8", "ignore")
    ids = sorted(set(re.findall(r"resourceId=(RS\d+)", html)))
    assert len(ids) == 1, f"{dataset}: expected one resource id, found {ids}"
    return ids[0]

DISB = {"DS00975": fone_resource("DS00975"), "DS00976": fone_resource("DS00976")}
print("disbursement resources:", DISB)

def disbursements_for(pid):
    out = []
    for ds, rs in DISB.items():
        for batch in http.fone_pages(ds, rs, filter_expr=f"project_id='{pid}'"):
            # Guard: if the API ignored the filter (the Projects API ignored its sector filter), we'd page through
            # ~1.6M rows per project. Stop immediately instead.
            wrong = [r.get("project_id") for r in batch if r.get("project_id") != pid]
            assert not wrong, f"{ds}: filter on project_id not applied (got {wrong[:3]}); stopping to avoid a full scan"
            out += [dict(r, _dataset=ds) for r in batch]
    return out

per_project("disbursements", disbursements_for)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Summary: copy back
# MAGIC Record counts per bronze table, for the checkpoint (c) report.

# COMMAND ----------

summary = {t.tableName: spark.table(f"{CATALOG}.bronze.{t.tableName}").count()
           for t in spark.sql(f"SHOW TABLES IN {CATALOG}.bronze").collect()}
summary["universe_projects"] = len(UNIVERSE)
print(json.dumps(summary, indent=2))
