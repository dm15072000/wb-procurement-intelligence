# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 00 — Verify workspace capabilities (Stage 2, read-only)
# MAGIC
# MAGIC **What this notebook does:** checks which Databricks features our design depends on actually work in this
# MAGIC Free Edition workspace. It only *reads* — it creates nothing, writes nothing and costs nothing.
# MAGIC
# MAGIC **Why:** the plan (`docs/01_plan.md`, risks R1/R2 and decision D6) assumes several features. Before building on
# MAGIC them, we prove each one works here. Copy the printed results back so they can be recorded in
# MAGIC `docs/04_data_sources.md`.

# COMMAND ----------

# Collect every check result in one dict so the last cell can print a single summary to copy back.
results = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Foundation Model endpoints
# MAGIC We need one model to generate answers and one to make embeddings. This checks both exist and are ready.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()  # inside a notebook this uses the notebook's own login — no token needed

for name in ["databricks-meta-llama-3-3-70b-instruct", "databricks-gte-large-en"]:
    try:
        ep = w.serving_endpoints.get(name)
        results[f"endpoint:{name}"] = str(ep.state.ready)
    except Exception as e:
        results[f"endpoint:{name}"] = f"ERROR {type(e).__name__}: {e}"
results

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Embedding call returns 1024 dimensions
# MAGIC CLAUDE.md says `gte-large-en` gives 1024-dimension vectors. One tiny call proves it (a few tokens, negligible).

# COMMAND ----------

try:
    resp = w.serving_endpoints.query(name="databricks-gte-large-en", input=["road rehabilitation contract"])
    results["embedding_dim"] = len(resp.data[0].embedding)
except Exception as e:
    results["embedding_dim"] = f"ERROR {type(e).__name__}: {e}"
results["embedding_dim"]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Vector Search endpoints
# MAGIC Decision D4: create one endpoint in Stage 5, but only after confirming none exists. This lists them.

# COMMAND ----------

try:
    results["vector_search_endpoints"] = [e.name for e in w.vector_search_endpoints.list_endpoints()]
except Exception as e:
    results["vector_search_endpoints"] = f"ERROR {type(e).__name__}: {e}"
results["vector_search_endpoints"]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Is `ai_parse_document` available? (decision D6)
# MAGIC The PDF-parser benchmark in Stage 3 needs to know whether this built-in SQL function exists on Free Edition.
# MAGIC `DESCRIBE FUNCTION` only looks the function up; it parses nothing.

# COMMAND ----------

try:
    rows = spark.sql("DESCRIBE FUNCTION ai_parse_document").collect()
    results["ai_parse_document"] = "AVAILABLE: " + rows[0][0][:120]
except Exception as e:
    results["ai_parse_document"] = f"NOT AVAILABLE ({type(e).__name__}): {str(e)[:200]}"
results["ai_parse_document"]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Can we create a catalog? (decision D3)
# MAGIC We look at the metastore privileges instead of creating anything. If `CREATE_CATALOG` (or ownership) is
# MAGIC missing, Stage 3 falls back to schemas inside catalog `workspace`.

# COMMAND ----------

try:
    me = w.current_user.me().user_name
    results["current_user"] = me
    grants = spark.sql("SHOW GRANTS ON METASTORE").collect()
    results["metastore_grants_for_me"] = [r.asDict() for r in grants if me in str(r.asDict())]
except Exception as e:
    results["metastore_grants_for_me"] = f"ERROR {type(e).__name__}: {str(e)[:200]}"
results.get("metastore_grants_for_me")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Databricks Apps available?
# MAGIC The UI is a Databricks App (Streamlit). Listing apps tells us the feature is enabled.

# COMMAND ----------

try:
    results["apps"] = [a.name for a in w.apps.list()]
except Exception as e:
    results["apps"] = f"ERROR {type(e).__name__}: {str(e)[:200]}"
results["apps"]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Summary — copy this output back
# MAGIC Paste the printed block into the chat so the results can be recorded as **verified** in `docs/04_data_sources.md`.

# COMMAND ----------

import json
print(json.dumps(results, indent=2, default=str))
