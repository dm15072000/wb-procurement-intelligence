# Databricks notebook source
# MAGIC %md
# MAGIC # 85 — KPI dashboard (AI/BI Lakeview), deployed from the repo
# MAGIC
# MAGIC **What this does:** (1) creates the small read-only views in `dashboards/kpi_views.sql` (schema `ops`, prefix
# MAGIC `kpi_`), (2) creates or updates the dashboard from `dashboards/procurement_kpis.lvdash.json` (built by
# MAGIC `dashboards/build_dashboard.py`), (3) publishes it, and (4) saves its id and link in `ops.config`
# MAGIC (`kpi_dashboard_id`, `kpi_dashboard_url`) so the app can link to it.
# MAGIC
# MAGIC **Why as code:** the dashboard definition is reviewed and versioned with the rest of the repo; this notebook is
# MAGIC the only way it reaches the workspace, and the weekly refresh job runs it last, so the dashboard always reads the
# MAGIC latest tables. Four pages: data health, model quality, assistant quality, usage & impact (test usage only;
# MAGIC business KPIs are PROPOSED pilot targets from docs/08, never invented numbers).

# COMMAND ----------

import json
import os
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient

CATALOG = "procurement"
O = f"{CATALOG}.ops"
REPO = os.path.abspath("..")
DISPLAY_NAME = "Procurement Intelligence KPIs"
dbutils.widgets.text("warehouse_id", "")          # empty = the first serverless SQL warehouse in the workspace
w = WorkspaceClient()
report = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Views (one statement per block; each reads existing tables only)

# COMMAND ----------

sql_text = open(os.path.join(REPO, "dashboards", "kpi_views.sql"), encoding="utf-8").read()
statements = [s.strip() for s in sql_text.split("\n;\n") if "CREATE" in s]
for s in statements:
    spark.sql(s)
views = [s.split("VIEW ")[1].split()[0] for s in statements]
report["views"] = {v: spark.table(v).count() for v in views}
print(json.dumps(report["views"], indent=1))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Create or update the dashboard, then publish it
# MAGIC The id is kept in `ops.config`, so re-runs update the same dashboard (and keep its link) instead of creating
# MAGIC copies. Published with the owner's credentials so viewers see the data without their own table grants.

# COMMAND ----------

serialized = open(os.path.join(REPO, "dashboards", "procurement_kpis.lvdash.json"), encoding="utf-8").read()
wh = dbutils.widgets.get("warehouse_id") or next(x.id for x in w.warehouses.list() if x.enable_serverless_compute)
me = w.current_user.me().user_name
cfg = {r.key: r.value for r in spark.table(f"{O}.config").collect()}
dash_id = cfg.get("kpi_dashboard_id")
if dash_id:
    try:
        w.api_client.do("GET", f"/api/2.0/lakeview/dashboards/{dash_id}")
    except Exception:
        dash_id = None                                # deleted or trashed: create a new one
if not dash_id:                                       # deployed before ops.config had the id: find it by name
    listing = w.api_client.do("GET", "/api/2.0/lakeview/dashboards", query={"page_size": 200})
    dash_id = next((d["dashboard_id"] for d in listing.get("dashboards", [])
                    if d.get("display_name") == DISPLAY_NAME and d.get("lifecycle_state") != "TRASHED"), None)
body = {"display_name": DISPLAY_NAME, "serialized_dashboard": serialized, "warehouse_id": wh}
if dash_id:
    w.api_client.do("PATCH", f"/api/2.0/lakeview/dashboards/{dash_id}", body=body)
    report["action"] = "updated"
else:
    dash_id = w.api_client.do("POST", "/api/2.0/lakeview/dashboards",
                              body=dict(body, parent_path=f"/Users/{me}"))["dashboard_id"]
    report["action"] = "created"
w.api_client.do("POST", f"/api/2.0/lakeview/dashboards/{dash_id}/published",
                body={"warehouse_id": wh, "embed_credentials": True})
host = w.config.host.rstrip("/")
url = f"{host}/dashboardsv3/{dash_id}/published"
now = datetime.now(timezone.utc)
spark.sql(f"DELETE FROM {O}.config WHERE key IN ('kpi_dashboard_id', 'kpi_dashboard_url')")
spark.createDataFrame([("kpi_dashboard_id", dash_id, now), ("kpi_dashboard_url", url, now)],
                      "key string, value string, updated_at timestamp").write.mode("append").saveAsTable(f"{O}.config")
report.update(dashboard_id=dash_id, url=url, warehouse_id=wh)
print(json.dumps(report, indent=1))
dbutils.notebook.exit(json.dumps(report))
