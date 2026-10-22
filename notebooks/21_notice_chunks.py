# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 21 — Procurement notice text as retrieval documents (checkpoint d change 3; L2 tender design)
# MAGIC
# MAGIC **What this notebook does:** turns the text of REOI, IFB and prequalification notices of the **16 slice
# MAGIC projects** into chunks with `doc_type = "Procurement Notice"`, in `docs.notice_chunks`. Notebook 60 embeds and
# MAGIC indexes them with the report chunks, so a team designing a new tender can retrieve past scopes and
# MAGIC qualification criteria (experience, turnover, key staff), cited by notice ID and reference number.
# MAGIC
# MAGIC **Why a separate table:** notebook 20 rebuilds `docs.chunks` from parsed PDFs; notices come from silver data,
# MAGIC not PDFs, so they keep their own table and their own re-build. No API calls (the text is already in
# MAGIC `silver.notices`). Exact repeats (the same text re-published) are flagged, latest copy primary.

# COMMAND ----------

import os, sys, json
from collections import Counter
sys.path.append(os.path.abspath(".."))
import importlib
import src.chunking.notices as nc
import src.chunking.dedup as dd
for m in (nc, dd):
    importlib.reload(m)

CATALOG = "procurement"
S, D = f"{CATALOG}.silver", f"{CATALOG}.docs"

notices = [r.asDict() for r in spark.sql(f"""
  SELECT n.* FROM {S}.notices n JOIN {S}.projects p ON n.project_id = p.project_id
  WHERE p.in_slice AND n.notice_type IN ({', '.join(repr(t) for t in nc.INDEXED_NOTICE_TYPES)})""").collect()]
chunks = [c for n in notices for c in nc.notice_chunks(n)]
dd.flag_repeats(chunks)     # exact repeats (re-published text): latest copy primary, older kept and flagged
report = {"slice_notices_of_indexed_types": len(notices),
          "notices_with_usable_text": len({c["doc_id"] for c in chunks}),
          "chunks": len(chunks), "primary_chunks": sum(c["is_primary"] for c in chunks),
          "by_notice_type": dict(Counter(c["section_path"].split(" > ")[0] for c in chunks)),
          "mentions_qualification": sum(any(w in c["text"].lower() for w in ("qualification", "experience", "turnover"))
                                        for c in chunks)}

# COMMAND ----------

spark.createDataFrame(chunks, "chunk_id string, doc_id string, seq int, chunk_type string, section_path string, "
                      "text string, context_header string, page_start int, page_end int, n_tokens int, "
                      "text_hash string, table_part string, merged_sections int, project_id string, doc_type string, "
                      "doc_date date, title string, notice_id string, dup_group string, dup_kind string, "
                      "is_primary boolean, is_duplicate boolean") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{D}.notice_chunks")
print(json.dumps(report, indent=2))
for c in chunks[:3]:
    print(c["context_header"], "\n", c["text"][:400], "\n")
