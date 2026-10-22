# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 55 — Ratings, lessons and procurement events from report text (Stage 5, M2.2, M3.3, L4)
# MAGIC
# MAGIC **What this notebook does:**
# MAGIC 1. **Ratings** (ISR / ICR / IEG) with regular expressions → `gold.ratings`, then the conflict rules →
# MAGIC    `gold.rating_conflicts` (`src/conflict/ratings.py`, design §5). Deterministic, no LLM.
# MAGIC 2. **Lessons** from ICR / IEG lessons sections and **procurement events** (amendment, re-bid, cancellation,
# MAGIC    delay, …) from ISR / ICR passages, with Llama 3.3 70B through `ai_query` → `gold.lessons`,
# MAGIC    `silver.contract_events`. Every item must quote its passage **word for word**, or it is dropped and counted
# MAGIC    (`src/extraction/llm_extract.py`, design §4 layer 8).
# MAGIC 3. Adds `Rating` and `Lesson` nodes with `RATES` / `TEACHES` edges to the graph.
# MAGIC
# MAGIC **Continuous learning:** only chunks not processed before (same `chunk_id` and `text_hash`) are sent to the LLM,
# MAGIC so a refresh that adds one new ICR costs only that ICR's passages. Passages are untrusted data: the prompts say
# MAGIC so, and tags inside them are neutralised.
# MAGIC
# MAGIC **Cost:** pay-per-token calls to the existing Foundation Model endpoint (no new serving endpoint). The keyword
# MAGIC pre-filter keeps the number of calls small; the count is printed before anything is sent.

# COMMAND ----------

import os, sys, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
sys.path.append(os.path.abspath(".."))
import importlib
import src.conflict.ratings as ra
import src.extraction.llm_extract as lx
import src.entity_resolution.keys as keys
for m in (ra, lx, keys):
    importlib.reload(m)
from pyspark.sql import functions as F

CATALOG = "procurement"
S, G, D, O = (f"{CATALOG}.{x}" for x in ("silver", "gold", "docs", "ops"))
LLM = "databricks-meta-llama-3-3-70b-instruct"
MAX_LLM_CALLS = 1500       # safety cap per run; raise it on purpose if the count below is larger
report = {}

chunks = [r.asDict() for r in spark.table(f"{D}.chunks").select(
    "chunk_id", "doc_id", "project_id", "doc_type", "doc_date", "section_path", "text", "text_hash", "is_primary").collect()]
print(len(chunks), "chunks")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Ratings (regex) and conflicts
# MAGIC One rating per dimension per passage, with the matched text kept. A sample is printed: **please check 20** to
# MAGIC confirm the "Previous | Current" table assumption (`ratings.py`).

# COMMAND ----------

RATED_TYPES = {ra.ISR_TYPE, ra.ICR_TYPE, *ra.IEG_TYPES}
ratings = []
for c in chunks:
    if c["doc_type"] in RATED_TYPES and c["project_id"]:
        for r in ra.extract_ratings(c["text"]):
            ratings.append({**r, "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "project_id": c["project_id"],
                            "doc_type": c["doc_type"], "doc_date": c["doc_date"]})
# A document can repeat its ratings (summary + table): keep one rating per (document, dimension), the first chunk's.
seen, uniq = set(), []
for r in sorted(ratings, key=lambda r: r["chunk_id"]):
    k = (r["doc_id"], r["dimension"])
    if k not in seen:
        seen.add(k)
        uniq.append(r)
ratings = uniq
spark.createDataFrame(ratings, "dimension string, value string, score int, matched string, chunk_id string, "
                      "doc_id string, project_id string, doc_type string, doc_date date") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.ratings")
report["ratings"] = {"rows": len(ratings), "by_doc_type_dimension": dict(Counter((r["doc_type"][:30], r["dimension"])
                                                                               for r in ratings).most_common())}
import random
random.seed(1)
for r in random.sample(ratings, min(20, len(ratings))):
    print(f"{r['doc_type'][:12]} {r['doc_date']} {r['dimension']:>15} = {r['value']:<26} | {r['matched'][:110]}")

# COMMAND ----------

by_proj = defaultdict(list)
for r in ratings:
    by_proj[r["project_id"]].append(r)
conflicts = []
for pid, obs in by_proj.items():
    for dim in sorted({o["dimension"] for o in obs}):
        res = ra.resolve(obs, dim, "outcome")
        for f in res["flags"]:
            conflicts.append({"project_id": pid, "dimension": dim, "flag": f["flag"], "detail": f["detail"],
                              "chunk_ids": [x for x in f["chunks"] if x],
                              "chosen_value": res["chosen"]["value"] if res["chosen"] else None,
                              "chosen_source": res["chosen"]["doc_type"] if res["chosen"] else None,
                              "rule": res["rule"]})
spark.createDataFrame(conflicts, "project_id string, dimension string, flag string, detail string, "
                      "chunk_ids array<string>, chosen_value string, chosen_source string, rule string") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.rating_conflicts")
report["conflicts"] = dict(Counter(c["flag"] for c in conflicts))
print(json.dumps(report["conflicts"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Which passages need the LLM (new ones only)
# MAGIC Lessons: ICR / IEG passages in lessons / findings / recommendations sections. Events: ISR / ICR / IEG / Project
# MAGIC Paper passages containing an event keyword (amend, re-bid, cancel, extension, …). Primary copies only.

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {O}.extraction_log
              (chunk_id STRING, text_hash STRING, kind STRING, n_kept INT, rejected STRING, processed_at TIMESTAMP)""")
done = {(r.chunk_id, r.text_hash, r.kind) for r in spark.table(f"{O}.extraction_log").collect()}
todo = []
for c in chunks:
    if not c["is_primary"]:
        continue
    if lx.wants_lessons(c) and (c["chunk_id"], c["text_hash"], "lessons") not in done:
        todo.append((c, "lessons", lx.lesson_prompt(c)))
    if lx.wants_events(c) and (c["chunk_id"], c["text_hash"], "events") not in done:
        todo.append((c, "events", lx.event_prompt(c)))
report["llm_calls_needed"] = dict(Counter(k for _, k, _ in todo))
print(report["llm_calls_needed"])
assert len(todo) <= MAX_LLM_CALLS, f"{len(todo)} calls > MAX_LLM_CALLS={MAX_LLM_CALLS}: check the filter or raise the cap"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Call the LLM with `ai_query` (temperature 0) and validate every item
# MAGIC `failOnError => false` returns errors per row instead of failing the whole batch; failed rows are retried on the
# MAGIC next run because they are not logged as done.

# COMMAND ----------

if todo:
    req = spark.createDataFrame([(c["chunk_id"], kind, p) for c, kind, p in todo], "chunk_id string, kind string, prompt string")
    req.createOrReplaceTempView("extraction_requests")
    replies = spark.sql(f"""
      SELECT chunk_id, kind,
             ai_query('{LLM}', prompt,
                      modelParameters => named_struct('temperature', 0.0, 'max_tokens', 1200),
                      failOnError => false) AS r
      FROM extraction_requests""").select("chunk_id", "kind", "r.result", "r.errorMessage").collect()
else:
    replies = []

by_id = {c["chunk_id"]: c for c in chunks}
lessons, events, log_rows = [], [], []
rejected_total, errors = Counter(), 0
now = datetime.now(timezone.utc)
for r in replies:
    if r.errorMessage or r.result is None:
        errors += 1
        continue
    c = by_id[r.chunk_id]
    kept, rej = lx.validate(r.result, c, r.kind)
    rejected_total.update(rej)
    for k in kept:
        k["text_hash"] = c["text_hash"]
    (lessons if r.kind == "lessons" else events).extend(kept)
    log_rows.append((r.chunk_id, c["text_hash"], r.kind, len(kept), json.dumps(rej), now))
report["llm"] = {"calls": len(replies), "errors_retry_next_run": errors, "lessons_kept": len(lessons),
                 "events_kept": len(events), "items_rejected": dict(rejected_total)}
print(json.dumps(report["llm"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Save: remove rows from passages that changed or disappeared, then append the new ones
# MAGIC Events are matched to a contract when their reference equals a contract reference of the same project.

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {G}.lessons (lesson STRING, topic STRING, quote STRING, chunk_id STRING,
              doc_id STRING, project_id STRING, doc_type STRING, doc_date DATE, text_hash STRING)""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {S}.contract_events (event_type STRING, contract_reference STRING,
              description STRING, quote STRING, chunk_id STRING, doc_id STRING, project_id STRING, doc_type STRING,
              doc_date DATE, text_hash STRING, matched_contract_key STRING)""")
spark.table(f"{D}.chunks").select("chunk_id", "text_hash").createOrReplaceTempView("current_chunks")
for t in (f"{G}.lessons", f"{S}.contract_events"):
    spark.sql(f"""DELETE FROM {t} WHERE NOT EXISTS (SELECT 1 FROM current_chunks c
                  WHERE c.chunk_id = {t}.chunk_id AND c.text_hash = {t}.text_hash)""")

refs = defaultdict(dict)
for r in spark.sql(f"SELECT contract_key, project_id, ref_key FROM {S}.contracts WHERE ref_key IS NOT NULL").collect():
    refs[r.project_id][r.ref_key] = r.contract_key
for e in events:
    rk = keys.ref_key(e.get("contract_reference"))
    e["matched_contract_key"] = refs.get(e["project_id"], {}).get(rk) if rk and len(rk) >= 4 else None

if lessons:
    spark.createDataFrame([{k: l.get(k) for k in ("lesson", "topic", "quote", "chunk_id", "doc_id", "project_id",
                                                  "doc_type", "doc_date", "text_hash")} for l in lessons],
                          spark.table(f"{G}.lessons").schema).write.mode("append").saveAsTable(f"{G}.lessons")
if events:
    spark.createDataFrame([{k: e.get(k) for k in ("event_type", "contract_reference", "description", "quote", "chunk_id",
                                                  "doc_id", "project_id", "doc_type", "doc_date", "text_hash",
                                                  "matched_contract_key")} for e in events],
                          spark.table(f"{S}.contract_events").schema).write.mode("append").saveAsTable(f"{S}.contract_events")
if log_rows:
    spark.createDataFrame(log_rows, "chunk_id string, text_hash string, kind string, n_kept int, rejected string, "
                          "processed_at timestamp").write.mode("append").saveAsTable(f"{O}.extraction_log")
report["events_matched_to_contract"] = sum(1 for e in events if e["matched_contract_key"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Graph: Rating and Lesson nodes, RATES / TEACHES edges (rebuilt each run)
# MAGIC Edges point to the project and to the chunk that holds the evidence (one hop to its section and document).

# COMMAND ----------

spark.sql(f"DELETE FROM {G}.nodes WHERE node_type IN ('Rating', 'Lesson')")
spark.sql(f"DELETE FROM {G}.edges WHERE edge_type IN ('RATES', 'TEACHES', 'RATING_EVIDENCE', 'LESSON_EVIDENCE')")
spark.sql(f"""INSERT INTO {G}.nodes
  SELECT concat('rating:', doc_id, ':', dimension), 'Rating', concat(dimension, ' = ', value),
         to_json(named_struct('score', score, 'doc_type', doc_type, 'matched', matched)), doc_date, 'gold.ratings'
  FROM {G}.ratings
  UNION ALL
  SELECT DISTINCT concat('lesson:', sha1(concat(chunk_id, lesson))), 'Lesson', substr(lesson, 1, 120),
         to_json(named_struct('topic', topic, 'quote', quote, 'doc_type', doc_type)), doc_date, 'gold.lessons'
  FROM {G}.lessons""")
spark.sql(f"""INSERT INTO {G}.edges
  SELECT concat('rating:', doc_id, ':', dimension), concat('project:', project_id), 'RATES', CAST(score AS DOUBLE),
         'regex_rating', chunk_id, doc_date FROM {G}.ratings
  WHERE concat('project:', project_id) IN (SELECT node_id FROM {G}.nodes)
  UNION ALL
  SELECT concat('rating:', doc_id, ':', dimension), concat('chunk:', chunk_id), 'RATING_EVIDENCE', NULL, 'regex_rating',
         matched, doc_date FROM {G}.ratings
  UNION ALL
  SELECT concat('lesson:', sha1(concat(chunk_id, lesson))), concat('project:', project_id), 'TEACHES', NULL,
         'llm_extraction+verbatim_quote', chunk_id, doc_date FROM {G}.lessons
  WHERE concat('project:', project_id) IN (SELECT node_id FROM {G}.nodes)
  UNION ALL
  SELECT concat('lesson:', sha1(concat(chunk_id, lesson))), concat('chunk:', chunk_id), 'LESSON_EVIDENCE', NULL,
         'llm_extraction+verbatim_quote', substr(quote, 1, 300), doc_date FROM {G}.lessons""")
orphans = spark.sql(f"""SELECT count(*) n FROM {G}.edges e LEFT ANTI JOIN {G}.nodes n ON e.dst_id = n.node_id
                        WHERE e.edge_type IN ('RATES', 'TEACHES', 'RATING_EVIDENCE', 'LESSON_EVIDENCE')""").first().n
report["graph"] = {"orphan_new_edges": orphans}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Review samples and summary
# MAGIC **Please judge 20 lessons and 20 events** (correct / wrong): that is the measured extraction precision.

# COMMAND ----------

for l in spark.table(f"{G}.lessons").orderBy(F.rand(3)).limit(20).collect():
    print(f"[{l.topic}] {l.lesson}\n    QUOTE: {l.quote[:200]}\n")
for e in spark.table(f"{S}.contract_events").orderBy(F.rand(3)).limit(20).collect():
    print(f"[{e.event_type}] {e.doc_type[:12]} {e.doc_date} ref={e.contract_reference} -> {e.matched_contract_key}\n"
          f"    QUOTE: {e.quote[:200]}\n")
print(json.dumps(report, indent=2, default=str))
