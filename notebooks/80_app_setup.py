# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 80 — App setup: logging tables, permissions, deployment steps (Stage 7, M4.2)
# MAGIC
# MAGIC **What this notebook does:** creates the tables the app writes to (`ops.feedback`, `ops.rag_requests`,
# MAGIC `ops.config`) and grants the app's service principal what it needs. It does **not** create or deploy the app:
# MAGIC you do that in the Apps UI (steps below), because CLAUDE.md says no deployment without your go-ahead.
# MAGIC
# MAGIC **Why feedback is a table:** 👍 / 👎 and "wrong source" comments become evaluation questions and negative
# MAGIC examples (design §7). Storing them in Delta makes them part of the same governed loop as everything else.

# COMMAND ----------

CATALOG = "procurement"
O = f"{CATALOG}.ops"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {O}")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {O}.feedback (ts STRING, user STRING, page STRING, status STRING, vote STRING,
              comment STRING, chunk_ids STRING)""")
spark.sql(f"CREATE TABLE IF NOT EXISTS {O}.rag_requests (ts STRING, user STRING, page STRING, payload STRING)")
spark.sql(f"CREATE TABLE IF NOT EXISTS {O}.config (key STRING, value STRING, updated_at TIMESTAMP)")
print(spark.sql(f"SHOW TABLES IN {O}").toPandas())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Apps limit check (read-only; checkpoint d change 7)
# MAGIC Free Edition allows **up to 3 Databricks Apps per account**, and an app **stops 24 hours** after it is started or
# MAGIC redeployed (Databricks docs, *Free Edition limitations*, checked 2026-10-21). This only lists apps: it stops or
# MAGIC deletes nothing.

# COMMAND ----------

from databricks.sdk import WorkspaceClient
APPS_LIMIT = 3
apps = list(WorkspaceClient().apps.list())
for a in apps:
    state = getattr(getattr(a, "compute_status", None), "state", None)
    print(f"- {a.name}: compute {state}")
if len(apps) >= APPS_LIMIT:
    print(f"\n{len(apps)} apps = the Free Edition limit of {APPS_LIMIT}. To create procurement-intelligence you must "
          f"STOP (does not free a slot) or DELETE one. Candidate: 'wbintelligence2' (earlier project). Your decision.")
else:
    print(f"\n{len(apps)} of {APPS_LIMIT} apps used: there is room; no need to stop or delete 'wbintelligence2'.")
print("Reminder: apps stop automatically after 24 hours; start the app again shortly before the demo.")
apps_summary = {"apps_used": len(apps), "limit": APPS_LIMIT,
                "apps": {a.name: str(getattr(getattr(a, "compute_status", None), "state", None)) for a in apps}}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Grants for the app's service principal
# MAGIC Fill in the app's service principal **application ID** (shown on the app's page after you create it), then run.
# MAGIC Read access to the data, write access to the two logging tables only.

# COMMAND ----------

APP_SP = ""   # e.g. "1a2b3c4d-...": the app's service principal application ID
if APP_SP:
    sp = f"`{APP_SP}`"
    for stmt in [f"GRANT USE CATALOG ON CATALOG {CATALOG} TO {sp}",
                 *[f"GRANT USE SCHEMA, SELECT ON SCHEMA {CATALOG}.{s} TO {sp}" for s in ("silver", "gold", "docs", "ml", "ops")],
                 f"GRANT MODIFY ON TABLE {O}.feedback TO {sp}",
                 f"GRANT MODIFY ON TABLE {O}.rag_requests TO {sp}",
                 f"GRANT EXECUTE ON MODEL {CATALOG}.ml.tender_duration TO {sp}"]:
        try:
            spark.sql(stmt)
            print("ok:", stmt)
        except Exception as e:
            print("FAILED:", stmt, "|", str(e)[:200])
else:
    print("Set APP_SP first.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deployment steps (you do these in the UI)
# MAGIC 1. Run notebooks 50, 51, 55, 60, 61, 70 first (the app reads their tables).
# MAGIC 2. **Compute → Apps → Create app → Custom**, name e.g. `procurement-intelligence`. Run the limit check above
# MAGIC    first: with fewer than 3 apps there is room and `wbintelligence2` can stay.
# MAGIC 3. **Resources:** add the SQL warehouse with resource key `sql-warehouse` (Can use); add serving endpoints
# MAGIC    `databricks-meta-llama-3-3-70b-instruct` and `databricks-gte-large-en` (Can query). If Vector Search is on,
# MAGIC    grant the service principal SELECT on `procurement.docs.chunk_index`.
# MAGIC 4. **Deploy** with source path = this Git folder's **root** (it contains `app.yaml`, `requirements.txt`, `app/`,
# MAGIC    `src/`). Set scikit-learn in `requirements.txt` to the version notebook 50 printed.
# MAGIC 5. Run the grants cell above with the app's service principal ID.
# MAGIC 6. Open the app. "Data coverage" shows whether Vector Search or the fallback is in use.

# COMMAND ----------

# MAGIC %md
# MAGIC Return the summary (apps used, grants applied or not), so a job run's result can be read without the notebook.

# COMMAND ----------

import json
dbutils.notebook.exit(json.dumps(dict(apps_summary, grants_applied=bool(APP_SP)), default=str))
