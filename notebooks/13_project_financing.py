# Databricks notebook source
# MAGIC %md
# MAGIC # 13 — Project development objectives and financing (co-financing), road-project universe
# MAGIC
# MAGIC **Why:** the assessment lists development objectives and co-financing structures. The Projects API has both
# MAGIC (`pdo`; `financier_loan` / `financierdesc` / `financiertype`), but notebook 10 did not request them.
# MAGIC **What this does:** one Projects API call per universe project (retries with exponential backoff, honours
# MAGIC HTTP 429 Retry-After, 1.5 s pause between calls; `src/ingest/http.py`), parsed by `src/ingest/financing.py`
# MAGIC (unit-tested) into `silver.project_pdo` and `silver.project_financing`. A financier whose type is not IBRD / IDA
# MAGIC is flagged as co-financing; a missing type stays NULL (never guessed). The raw replies are kept in bronze.

# COMMAND ----------

import json, os, sys, urllib.parse
from datetime import datetime, timezone
sys.path.append(os.path.abspath(".."))
import importlib
import src.ingest.http as http
import src.ingest.financing as fn
importlib.reload(fn)

CATALOG = "procurement"
pids = [r.project_id for r in spark.sql(f"SELECT project_id FROM {CATALOG}.silver.projects WHERE in_universe").collect()]
print(len(pids), "universe projects")

# COMMAND ----------

raw, pdo_rows, fin_rows, failed = [], [], [], []
for pid in pids:
    url = "https://search.worldbank.org/api/v2/projects?" + urllib.parse.urlencode(
        {"format": "json", "id": pid, "fl": fn.FIELDS}, safe=",")
    try:
        d = http.get(url)
    except Exception as e:                      # reported, not hidden
        failed.append((pid, str(e)[:200]))
        continue
    for p in (d.get("projects") or {}).values():
        if p.get("id") != pid:
            continue
        raw.append((pid, json.dumps(p), datetime.now(timezone.utc)))
        pdo_rows.append(fn.pdo_row(p))
        fin_rows.extend(fn.financing_rows(p))

spark.createDataFrame(raw, "project_id string, record_json string, ingested_at timestamp") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.bronze.project_financing_raw")
spark.createDataFrame(pdo_rows, "project_id string, pdo string") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.silver.project_pdo")
spark.createDataFrame(fin_rows, "project_id string, loan_code string, financier string, financier_type string, "
                                "is_cofinancing boolean") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.silver.project_financing")

# COMMAND ----------

report = {
    "projects": len(pids), "fetched": len(raw), "failed": failed,
    "with_pdo": sum(1 for _, t in pdo_rows if t),
    "financier_rows": len(fin_rows),
    "projects_with_cofinancing": len({r["project_id"] for r in fin_rows if r["is_cofinancing"]}),
    "financier_types": {t: sum(1 for r in fin_rows if r["financier_type"] == t)
                        for t in sorted({r["financier_type"] for r in fin_rows if r["financier_type"]})},
}
print(json.dumps(report, indent=1))
dbutils.notebook.exit(json.dumps(report))
