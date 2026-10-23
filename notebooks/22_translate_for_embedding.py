# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 22 — Translate non-English passages to English, for EMBEDDING ONLY
# MAGIC
# MAGIC **Why:** the embedding model `databricks-gte-large-en` is trained on English. A French passage (Mali notices,
# MAGIC procurement plans, part of one ICR) gets a poor vector, so an English question rarely finds it.
# MAGIC **What this does:** detects the language of every report and notice passage (`src/chunking/language.py`,
# MAGIC unit-tested), saves the counts, and translates the French passages to English with Llama 3.3 70B. The
# MAGIC translation is stored NEXT TO the original in `docs.chunk_translations`; notebook 60 embeds the translation, but
# MAGIC the text shown, cited and quoted everywhere stays the **original** (citations and verbatim-quote checks use it).
# MAGIC
# MAGIC **Cost guard:** widget `mode` = **dry_run** (default) only counts. `mode` = translate needs `approved_calls` ≥ the
# MAGIC number of passages to translate. Resumable: a passage already translated with the same `text_hash` is skipped.
# MAGIC Each translation is checked: the share of the original's numbers kept (`numbers_kept`) is stored and reported.
# MAGIC Production option (proposal): a multilingual embedding model instead of translating.

# COMMAND ----------

import os, sys, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
sys.path.append(os.path.abspath(".."))
import importlib
import src.chunking.language as lg
importlib.reload(lg)

CATALOG = "procurement"
D, O = f"{CATALOG}.docs", f"{CATALOG}.ops"
LLM = "databricks-meta-llama-3-3-70b-instruct"
BATCH_SIZE = 50
dbutils.widgets.dropdown("mode", "dry_run", ["dry_run", "translate"])
dbutils.widgets.text("approved_calls", "0")
MODE, APPROVED_CALLS = dbutils.widgets.get("mode"), int(dbutils.widgets.get("approved_calls") or 0)
report = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Language of every passage (reports + procurement notices), counted by document type

# COMMAND ----------

rows = [r.asDict() for r in spark.sql(f"""
  SELECT chunk_id, text_hash, doc_type, text FROM {D}.chunks
  UNION ALL SELECT chunk_id, text_hash, doc_type, text FROM {D}.notice_chunks""").collect()]
for r in rows:
    r["lang"] = lg.detect(r["text"])
spark.createDataFrame([(r["chunk_id"], r["text_hash"], r["doc_type"], r["lang"]) for r in rows],
                      "chunk_id string, text_hash string, doc_type string, lang string") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{D}.chunk_language")
by_type = defaultdict(Counter)
for r in rows:
    by_type[r["doc_type"]][r["lang"]] += 1
report["passages"] = len(rows)
report["by_language"] = dict(Counter(r["lang"] for r in rows))
report["by_doc_type"] = {k: dict(v) for k, v in by_type.items()}
print(json.dumps(report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Which passages to translate (dry run stops here)
# MAGIC French only. "other" passages are tables, figures, name lists or a non-French language with no running text worth
# MAGIC translating (checked on samples, 2026-10-22).

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {D}.chunk_translations (chunk_id STRING, text_hash STRING, source_lang STRING,
              translated_text STRING, numbers_kept DOUBLE, model STRING, translated_at TIMESTAMP)""")
done = {(r.chunk_id, r.text_hash) for r in spark.table(f"{D}.chunk_translations").collect()}
todo = [r for r in rows if r["lang"] == "fr" and (r["chunk_id"], r["text_hash"]) not in done]
report["french_passages"] = sum(r["lang"] == "fr" for r in rows)
report["already_translated"] = report["french_passages"] - len(todo)
report["llm_calls_this_run"] = len(todo)
print(f"FRENCH PASSAGES: {report['french_passages']} | ALREADY TRANSLATED: {report['already_translated']} | "
      f"LLM CALLS THIS RUN: {len(todo)} (endpoint {LLM})")
if MODE == "dry_run":
    dbutils.notebook.exit(json.dumps(report, default=str))
assert len(todo) <= APPROVED_CALLS, f"{len(todo)} calls planned but approved_calls = {APPROVED_CALLS}"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Translate in resumable batches (saved after every batch)

# COMMAND ----------

totals = Counter()
for i in range(0, len(todo), BATCH_SIZE):
    batch = todo[i:i + BATCH_SIZE]
    spark.createDataFrame([(r["chunk_id"], lg.translate_prompt(r["text"])) for r in batch],
                          "chunk_id string, prompt string").createOrReplaceTempView("tr_requests")
    replies = {r.chunk_id: (r.result, r.errorMessage) for r in spark.sql(f"""
      SELECT chunk_id, ai_query('{LLM}', prompt,
                                modelParameters => named_struct('temperature', 0.0, 'max_tokens', 4000),
                                failOnError => false) AS r
      FROM tr_requests""").select("chunk_id", "r.result", "r.errorMessage").collect()}
    out = []
    for r in batch:
        res, err = replies.get(r["chunk_id"], (None, "missing"))
        if err or not (res or "").strip():
            totals["errors"] += 1           # not saved -> retried on the next run
            continue
        out.append((r["chunk_id"], r["text_hash"], "fr", res.strip(), lg.numbers_kept(r["text"], res), LLM,
                    datetime.now(timezone.utc)))
    if out:
        ids = ", ".join(repr(x[0]) for x in out)
        spark.sql(f"DELETE FROM {D}.chunk_translations WHERE chunk_id IN ({ids})")
        spark.createDataFrame(out, "chunk_id string, text_hash string, source_lang string, translated_text string, "
                                   "numbers_kept double, model string, translated_at timestamp") \
             .write.mode("append").saveAsTable(f"{D}.chunk_translations")
    totals["translated"] += len(out)
    print(f"batch {i // BATCH_SIZE + 1}: {len(out)} translated, {len(batch) - len(out)} errors")
    if batch and not out:
        print("STOPPING EARLY: every call in the batch failed (quota / endpoint); resume later")
        break
kept = [r.numbers_kept for r in spark.table(f"{D}.chunk_translations").collect()]
report["translated_this_run"] = totals["translated"]
report["errors_retry_next_run"] = totals["errors"]
report["numbers_kept"] = {"mean": round(sum(kept) / len(kept), 3) if kept else None,
                          "below_0.9": sum(k < 0.9 for k in kept), "translations": len(kept)}
print(json.dumps(report, indent=2, default=str))
dbutils.notebook.exit(json.dumps(report, default=str))
