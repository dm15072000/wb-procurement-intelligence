# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 60 — Embeddings and the Vector Search index (Stage 5, M1.5, M2.4, M4.1)
# MAGIC
# MAGIC **What this notebook does:**
# MAGIC 1. Builds `docs.chunk_embeddings`: every chunk with its citation fields (title, URL, page) and filter columns
# MAGIC    (project, country, doc type, date as a number, primary copy), plus a 1,024-number embedding from
# MAGIC    `databricks-gte-large-en`. **Incremental:** only new or changed chunks (by `chunk_id` + `text_hash`) are embedded.
# MAGIC 2. Creates the Vector Search endpoint and a **Delta Sync** index over that table (Free Edition: one endpoint,
# MAGIC    Delta Sync only). The index follows the table, so new chunks become searchable after a sync: that is the
# MAGIC    retrieval side of the continuous-learning loop.
# MAGIC 3. Tests a query through Vector Search and through the **fallback** (cosine similarity over the same table), so
# MAGIC    the demo still works if the endpoint is unavailable.
# MAGIC
# MAGIC **Approval:** creating the endpoint was approved as decision D4 (2026-10-20). It still needs `CREATE_ENDPOINT = True`
# MAGIC below, and the notebook checks first that no other endpoint exists (Free Edition allows one).

# COMMAND ----------

# MAGIC %md
# MAGIC The client is **pinned** (2026-10-22): an unpinned install pulled a version that downgraded protobuf
# MAGIC 6.33.5 → 5.29.6, and the Python kernel of serverless environment 6 then failed to start. Version 0.66
# MAGIC accepts the runtime's protobuf 6.

# COMMAND ----------

# MAGIC %pip install "databricks-vectorsearch==0.66" "protobuf>=6.33.5,<7" --quiet

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, json, time, datetime
sys.path.append(os.path.abspath(".."))
import importlib
import src.rag.retrieval as rt
import src.rag.databricks_tools as dt
for m in (rt, dt):
    importlib.reload(m)
from pyspark.sql import functions as F

CATALOG = "procurement"
D = f"{CATALOG}.docs"
EMB = f"{D}.chunk_embeddings"
# Create the Vector Search endpoint + index only when the `create_endpoint` widget / job parameter is "true" (default
# "false"). Approved: D4 (2026-10-20) and the user's run instruction of 2026-10-22.
dbutils.widgets.dropdown("create_endpoint", "false", ["false", "true"])
CREATE_ENDPOINT = dbutils.widgets.get("create_endpoint") == "true"
report = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Probe: does `ai_query` return 1,024-number embeddings here? (verifies the call before the bulk run)

# COMMAND ----------

probe = spark.sql(f"SELECT ai_query('{dt.EMBED_ENDPOINT}', 'rural road maintenance contract') AS e").first().e
report["embedding_dimension"] = len(probe)
assert len(probe) == 1024, f"unexpected embedding size {len(probe)}"
print(report)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Source rows: chunk + citation fields + filter columns
# MAGIC - `country_iso3`: the project's first country; for country-level CPFs, the country named on the document.
# MAGIC - `doc_date_int` (yyyymmdd): Vector Search filters compare numbers reliably, so the "as of" date rule is numeric.
# MAGIC - `embed_text` = context header ("[ICR | P147864 | 2019-06-30] V. LESSONS > Procurement") + text, so the
# MAGIC   vector knows which document and section the passage came from.
# MAGIC - **Procurement notices** (checkpoint d change 3): REOI / IFB text from `docs.notice_chunks` (notebook 21) is
# MAGIC   indexed alongside, as doc_type "Procurement Notice", cited by notice ID (no URL).
# MAGIC - **French passages** (2026-10-22): the embedding model is English-only, so a passage with an English translation
# MAGIC   in `docs.chunk_translations` (notebook 22) is embedded FROM THE TRANSLATION. Only the vector changes: `text`
# MAGIC   (shown, cited and quoted) is always the original. `embedded_from` records which text was embedded.

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {D}.chunk_translations (chunk_id STRING, text_hash STRING, source_lang STRING,
              translated_text STRING, numbers_kept DOUBLE, model STRING, translated_at TIMESTAMP)""")
TRANSLATIONS = f"""
  LEFT JOIN {D}.chunk_translations t ON t.chunk_id = k.chunk_id AND t.text_hash = k.text_hash"""
notice_sql = ""
if spark.catalog.tableExists(f"{D}.notice_chunks"):
    notice_sql = f"""
  UNION ALL
  SELECT k.chunk_id, k.doc_id, k.project_id, element_at(p.country_iso3_list, 1) AS country_iso3,
         k.doc_type, k.doc_date, CAST(date_format(k.doc_date, 'yyyyMMdd') AS INT) AS doc_date_int,
         k.section_path, k.chunk_type, k.text, k.text_hash, k.is_primary, k.page_start, k.page_end,
         concat('Procurement notice ', k.notice_id, ': ', k.title) AS title, CAST(NULL AS STRING) AS pdf_url,
         concat(k.context_header, '\\n', coalesce(t.translated_text, k.text)) AS embed_text,
         CASE WHEN t.translated_text IS NOT NULL THEN 'english_translation' ELSE 'original' END AS embedded_from
  FROM {D}.notice_chunks k
  LEFT JOIN {CATALOG}.silver.projects p ON k.project_id = p.project_id{TRANSLATIONS}"""
else:
    print("docs.notice_chunks not found: run notebook 21 to index procurement notices")
source = spark.sql(f"""
  SELECT k.chunk_id, k.doc_id, k.project_id,
         coalesce(element_at(p.country_iso3_list, 1), cn.iso3) AS country_iso3,
         k.doc_type, k.doc_date, CAST(date_format(k.doc_date, 'yyyyMMdd') AS INT) AS doc_date_int,
         k.section_path, k.chunk_type, k.text, k.text_hash, k.is_primary, k.page_start, k.page_end,
         d.title, d.pdf_url, concat(k.context_header, '\\n', coalesce(t.translated_text, k.text)) AS embed_text,
         CASE WHEN t.translated_text IS NOT NULL THEN 'english_translation' ELSE 'original' END AS embedded_from
  FROM {D}.chunks k
  JOIN {D}.documents d ON k.doc_id = d.doc_id
  LEFT JOIN {CATALOG}.silver.projects p ON k.project_id = p.project_id
  LEFT JOIN {CATALOG}.silver.countries cn ON d.country = cn.name{TRANSLATIONS}{notice_sql}""") \
    .withColumn("embed_hash", F.sha2(F.col("embed_text"), 256))
source.createOrReplaceTempView("embed_source")
report["embedded_from_translation"] = source.where("embedded_from = 'english_translation'").count()
report["chunks"] = source.count()
report["chunks_by_doc_type"] = {r.doc_type: r.n for r in source.groupBy("doc_type").count()
                                .withColumnRenamed("count", "n").collect()}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Embed only new or changed chunks, then MERGE (Change Data Feed on, for Delta Sync)

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {EMB} (
  chunk_id STRING NOT NULL, doc_id STRING, project_id STRING, country_iso3 STRING, doc_type STRING, doc_date DATE,
  doc_date_int INT, section_path STRING, chunk_type STRING, text STRING, text_hash STRING, is_primary BOOLEAN,
  page_start INT, page_end INT, title STRING, pdf_url STRING, embedding ARRAY<FLOAT>, embedded_at TIMESTAMP,
  embedded_from STRING, embed_hash STRING)
  TBLPROPERTIES (delta.enableChangeDataFeed = true)""")
if "embed_hash" not in spark.table(EMB).columns:          # tables created before 2026-10-22
    spark.sql(f"ALTER TABLE {EMB} ADD COLUMNS (embedded_from STRING, embed_hash STRING)")
# Re-embed: new or changed passages, and passages whose embedded text changed between original and translation
# (rows embedded before 2026-10-22 have no embed_hash and count as embedded from the original).
todo = spark.sql(f"""SELECT s.* FROM embed_source s LEFT JOIN {EMB} e ON s.chunk_id = e.chunk_id
                     WHERE e.chunk_id IS NULL OR e.text_hash <> s.text_hash
                        OR coalesce(e.embedded_from, 'original') <> s.embedded_from
                        OR (s.embedded_from = 'english_translation' AND coalesce(e.embed_hash, '') <> s.embed_hash)""")
n_new = todo.count()
report["chunks_to_embed"] = n_new
print(n_new, "chunks to embed")
if n_new:
    todo.createOrReplaceTempView("embed_todo")
    # Embeddings are written to a staging table FIRST: a MERGE may read its source more than once, and each read of
    # a view with ai_query would call (and pay for) the endpoint again.
    spark.sql(f"""CREATE OR REPLACE TABLE {D}._embed_staging AS
      SELECT * EXCEPT (embed_text), CAST(ai_query('{dt.EMBED_ENDPOINT}', embed_text) AS ARRAY<FLOAT>) AS embedding,
             current_timestamp() AS embedded_at
      FROM embed_todo""")
    spark.sql(f"""MERGE INTO {EMB} t USING {D}._embed_staging s ON t.chunk_id = s.chunk_id
                  WHEN MATCHED THEN UPDATE SET *
                  WHEN NOT MATCHED THEN INSERT *""")
    spark.sql(f"DROP TABLE {D}._embed_staging")
# Chunks that no longer exist (re-chunked documents) are removed, so the index never cites a vanished passage.
spark.sql(f"DELETE FROM {EMB} WHERE chunk_id NOT IN (SELECT chunk_id FROM embed_source)")
# Metadata (e.g. is_primary after a duplicate re-flag) is refreshed without re-embedding.
spark.sql(f"""MERGE INTO {EMB} t USING embed_source s ON t.chunk_id = s.chunk_id AND t.text_hash = s.text_hash
              WHEN MATCHED AND (t.is_primary <> s.is_primary OR NOT (t.country_iso3 <=> s.country_iso3))
              THEN UPDATE SET t.is_primary = s.is_primary, t.country_iso3 = s.country_iso3""")
report["embedded_rows"] = spark.table(EMB).count()
report["null_embeddings"] = spark.table(EMB).where("embedding IS NULL OR size(embedding) <> 1024").count()
print(json.dumps(report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Vector Search endpoint and Delta Sync index (D4; flag above)
# MAGIC Self-managed embeddings: the index reads the `embedding` column we computed, so Vector Search and the fallback
# MAGIC use identical vectors. Triggered sync: the refresh job syncs after each embedding run.

# COMMAND ----------

from databricks.vector_search.client import VectorSearchClient
vsc = VectorSearchClient(disable_notice=True)
existing = [e["name"] for e in vsc.list_endpoints().get("endpoints", [])]
print("existing endpoints:", existing)
COLUMNS_TO_SYNC = rt.COLUMNS + ["doc_date_int", "is_primary"]

if CREATE_ENDPOINT:
    if dt.VS_ENDPOINT not in existing:
        assert not existing, f"Free Edition allows one endpoint and one exists already: {existing}. Ask before replacing it."
        vsc.create_endpoint_and_wait(name=dt.VS_ENDPOINT, endpoint_type="STANDARD")
    try:
        idx = vsc.get_index(dt.VS_ENDPOINT, dt.VS_INDEX)
        idx.sync()
        print("index exists: sync triggered")
    except Exception:
        idx = vsc.create_delta_sync_index_and_wait(
            endpoint_name=dt.VS_ENDPOINT, index_name=dt.VS_INDEX, source_table_name=EMB, primary_key="chunk_id",
            pipeline_type="TRIGGERED", embedding_vector_column="embedding", embedding_dimension=1024,
            columns_to_sync=COLUMNS_TO_SYNC)
        print("index created")
elif dt.VS_ENDPOINT in existing:
    idx = vsc.get_index(dt.VS_ENDPOINT, dt.VS_INDEX)
    idx.sync()
else:
    idx = None
    print("Vector Search not enabled: the fallback below is used.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Test query: Vector Search and fallback must agree on the top passages

# COMMAND ----------

from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
backend_fb = dt.DatabricksBackend(dt.spark_sql_runner(spark), w,
                                  fallback_rows=dt.fallback_rows_from(r.asDict() for r in spark.table(EMB).collect()))
q = "lessons on packaging civil works contracts to attract more bidders"
vec = backend_fb.embed(q)
f = rt.filters_for(as_of="2030-01-01", doc_types=rt.LESSON_DOC_TYPES)
fb_hits = backend_fb.search(vec, f, 5)
for h in fb_hits:
    print(f"fallback {h['score']:.3f} {h['doc_type'][:12]} {h['doc_date']} {h['section_path'][:60]}")
if idx is not None:
    for _ in range(30):                      # wait for the triggered sync to finish
        st = idx.describe().get("status", {})
        if st.get("ready"):
            break
        time.sleep(20)
    vs_hits = dt.DatabricksBackend(dt.spark_sql_runner(spark), w, vs_index=idx).search(vec, f, 5)
    for h in vs_hits:
        print(f"vector   {h['score']:.3f} {h['doc_type'][:12]} {h['doc_date']} {h['section_path'][:60]}")
    report["top5_overlap_vs_fallback"] = len({h["chunk_id"] for h in vs_hits} & {h["chunk_id"] for h in fb_hits})

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Hybrid (keyword + vector) check (checkpoint d change 2)
# MAGIC Is Vector Search HYBRID mode accepted for this self-managed index? Either way, a query naming a contract
# MAGIC reference must find a passage containing it. The result is saved to `ops.config` for the app and notebook 61.

# COMMAND ----------

ref_row = spark.sql(f"""SELECT evidence FROM {CATALOG}.gold.edges WHERE edge_type = 'DESCRIBES'
                        AND evidence LIKE '%|ref:%' LIMIT 1""").collect()
ref = ref_row[0].evidence.split("|ref:", 1)[1].split(" |")[0] if ref_row else None
q2 = f"What happened to contract {ref}?" if ref else "What happened to contract GYTJ201B?"
vec2 = backend_fb.embed(q2)
f2 = rt.filters_for(as_of="2030-01-01")
fb_kw = backend_fb.keyword_search(q2, f2, 5)
report["keyword_fallback_identifier_found"] = any(h.get("id_match") for h in fb_kw)
if idx is not None:
    b = dt.DatabricksBackend(dt.spark_sql_runner(spark), w, vs_index=idx)
    hy = b.search(vec2, f2, 5, q2)
    report["vector_search_hybrid_supported"] = b.hybrid_supported
    import src.rag.keyword as kwm
    report["hybrid_identifier_in_top5"] = any(kwm.id_match(h.get("text"), kwm.identifiers(q2)) for h in hy)
spark.sql(f"CREATE TABLE IF NOT EXISTS {CATALOG}.ops.config (key STRING, value STRING, updated_at TIMESTAMP)")
spark.sql(f"DELETE FROM {CATALOG}.ops.config WHERE key = 'vs_hybrid_supported'")
spark.sql(f"""INSERT INTO {CATALOG}.ops.config VALUES ('vs_hybrid_supported',
              '{report.get("vector_search_hybrid_supported")}', current_timestamp())""")
print(q2)
print(json.dumps(report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC Save the summary (readable from a job run) and return it.

# COMMAND ----------

spark.createDataFrame([(datetime.datetime.now(datetime.timezone.utc), json.dumps(report, default=str))],
                      "run_ts timestamp, summary string").write.mode("append").saveAsTable(f"{CATALOG}.ops.embedding_runs")
dbutils.notebook.exit(json.dumps(report, default=str))
