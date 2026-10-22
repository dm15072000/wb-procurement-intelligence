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
# MAGIC 2. **Lessons** and **procurement events** (amendment, re-bid, cancellation, delay, …) with Llama 3.3 70B
# MAGIC    through `ai_query`, **one call per passage returning both** → `gold.lessons`, `silver.contract_events`. Every
# MAGIC    item must quote its passage **word for word**, or it is dropped and counted (`src/extraction/llm_extract.py`,
# MAGIC    design §4 layer 8).
# MAGIC 3. Adds `Rating` and `Lesson` nodes with `RATES` / `TEACHES` edges to the graph.
# MAGIC
# MAGIC **Continuous learning:** only chunks not processed before (same `chunk_id` and `text_hash`) are sent to the LLM,
# MAGIC so a refresh that adds one new ICR costs only that ICR's passages. Passages are untrusted data: the prompts say
# MAGIC so, and tags inside them are neutralised.
# MAGIC
# MAGIC **Cost:** pay-per-token calls to the existing Foundation Model endpoint (no new serving endpoint). All reporting
# MAGIC documents are kept (every ISR); each passage is **pre-filtered** by section and trigger words (user decision
# MAGIC 2026-10-22). Section 2 is a dry run that prints total chunks, chunks after the filter and the number of calls;
# MAGIC the notebook then STOPS (widget `mode` = dry_run, the default). To extract, set `mode` = extract and copy the number
# MAGIC into `APPROVED_CALLS`; section 3 will not start otherwise. It runs in batches of 100 and saves
# MAGIC after each, so it can be stopped and resumed safely. Ratings stay regex-only (section 1).

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
LLM = "databricks-meta-llama-3-3-70b-instruct"   # Foundation Model API, pay-per-token (Meta Llama 3.3 70B Instruct)
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
# MAGIC ## 2. Which passages need the LLM: DRY RUN (no LLM call in this cell)
# MAGIC **All documents of the reporting types are kept** (ICR, ICR Review, PPAR, every ISR, Project Papers, PADs).
# MAGIC Procurement Plans (labels already parsed in notebook 20) and country CPFs are not sent. Repeat copies (exact /
# MAGIC near duplicates, same text) are skipped. A passage is sent only if (user decision 2026-10-22):
# MAGIC - its **section** is relevant: procurement, implementation, lessons, key issues, risks; or
# MAGIC - its **text** has a trigger word: rebid, re-tender, cancel, amend, variation order, delay, extension of time,
# MAGIC   terminat, lesson. French too: *relance, infructueux, annul, avenant, ordre de modification, retard,
# MAGIC   prolongation / prorogation, résili, leçon, enseignement* (and sections *passation des marchés, mise en œuvre,
# MAGIC   leçons, risques, problèmes clés*). Accents and the "œ" ligature are folded.
# MAGIC
# MAGIC One LLM call per passage (lessons and events together). Chunks already in `ops.extraction_log` with the same
# MAGIC `text_hash` are skipped, so a re-run continues where the last one stopped.
# MAGIC **Read the counts printed below, then set `APPROVED_CALLS` in section 3 to the number of calls.**

# COMMAND ----------

BATCH_SIZE = 100               # calls per ai_query batch; results are saved after every batch
spark.sql(f"""CREATE TABLE IF NOT EXISTS {O}.extraction_log
              (chunk_id STRING, text_hash STRING, kind STRING, n_kept INT, rejected STRING, processed_at TIMESTAMP)""")
done = {(r.chunk_id, r.text_hash, r.kind) for r in spark.table(f"{O}.extraction_log").collect()}
todo, scope_info = lx.select_requests(chunks, done=done)
report["llm_scope"] = scope_info
print(json.dumps(scope_info, indent=2, ensure_ascii=False))
n_calls = scope_info["estimated_llm_calls_this_run"]
print(f"\nTOTAL CHUNKS: {scope_info['total_chunks']:,} | AFTER FILTER: {scope_info['chunks_after_filter']:,} | "
      f"LLM CALLS THIS RUN: {n_calls:,} in {-(-n_calls // BATCH_SIZE)} batches of {BATCH_SIZE} (endpoint {LLM})")
# A small sample of passages that passed and that were filtered out, so the filter can be checked by eye.
import random
random.seed(2)
passed_ids = {c["chunk_id"] for c in todo}
for label, pool in (("PASSED", [c for c in chunks if c["chunk_id"] in passed_ids]),
                    ("FILTERED OUT", [c for c in chunks if c.get("is_primary") and c["doc_type"] in lx.EXTRACT_DOC_TYPES
                                      and not lx.prefilter(c)])):
    print(f"\n--- 5 {label} ---")
    for c in random.sample(pool, min(5, len(pool))):
        print(f"{c['doc_type'][:14]} | {(c['section_path'] or '')[:60]} | {' '.join(c['text'].split())[:120]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Stop here in dry-run mode (the default)
# MAGIC The `mode` widget is **dry_run** by default: the counts above are saved to `ops.extraction_dry_run` and the
# MAGIC notebook stops cleanly, with no LLM call. To extract, set the widget to **extract** and set `APPROVED_CALLS` below.

# COMMAND ----------

dbutils.widgets.dropdown("mode", "dry_run", ["dry_run", "extract"])
dry = {k: v for k, v in scope_info.items()}
spark.createDataFrame([(datetime.now(timezone.utc), json.dumps(dry, ensure_ascii=False), LLM)],
                      "run_ts timestamp, scope_json string, endpoint string") \
     .write.mode("append").saveAsTable(f"{O}.extraction_dry_run")
if dbutils.widgets.get("mode") == "dry_run":
    dbutils.notebook.exit(json.dumps({"mode": "dry_run", "total_chunks": dry["total_chunks"],
                                      "chunks_after_filter": dry["chunks_after_filter"],
                                      "llm_calls_this_run": dry["estimated_llm_calls_this_run"]}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Call the LLM in resumable batches, validate, and save after EVERY batch
# MAGIC Each batch: `ai_query` (temperature 0, `failOnError => false` so one bad row does not fail the batch) →
# MAGIC verbatim-quote validation → append lessons / events → log the processed chunks. If the notebook stops (quota,
# MAGIC timeout, cancel), everything saved so far is kept and the next run starts with the next unprocessed chunk.
# MAGIC Rows that returned an error are **not** logged, so they are retried on the next run.
# MAGIC First, rows from passages that changed or disappeared since the last run are removed.
# MAGIC
# MAGIC **Set `APPROVED_CALLS` to the number printed by section 2.** The cell stops if the run would make more calls
# MAGIC than approved (0 = nothing approved yet).

# COMMAND ----------

APPROVED_CALLS = 0   # <- copy "LLM CALLS THIS RUN" from section 2 here once you are happy with it
assert len(todo) <= APPROVED_CALLS, (f"{len(todo)} LLM calls planned but APPROVED_CALLS = {APPROVED_CALLS}: "
                                     "check the section 2 counts, then set APPROVED_CALLS")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {G}.lessons (lesson STRING, topic STRING, quote STRING, chunk_id STRING,
              doc_id STRING, project_id STRING, doc_type STRING, doc_date DATE, text_hash STRING)""")
spark.sql(f"""CREATE TABLE IF NOT EXISTS {S}.contract_events (event_type STRING, contract_reference STRING,
              description STRING, quote STRING, chunk_id STRING, doc_id STRING, project_id STRING, doc_type STRING,
              doc_date DATE, text_hash STRING, matched_contract_key STRING)""")
spark.table(f"{D}.chunks").select("chunk_id", "text_hash").createOrReplaceTempView("current_chunks")
for t in (f"{G}.lessons", f"{S}.contract_events"):
    spark.sql(f"""DELETE FROM {t} WHERE NOT EXISTS (SELECT 1 FROM current_chunks c
                  WHERE c.chunk_id = {t}.chunk_id AND c.text_hash = {t}.text_hash)""")
LESSON_SCHEMA, EVENT_SCHEMA = spark.table(f"{G}.lessons").schema, spark.table(f"{S}.contract_events").schema
LESSON_COLS = [f.name for f in LESSON_SCHEMA]
EVENT_COLS = [f.name for f in EVENT_SCHEMA]

# Contract references per project, to link an extracted event to a contract.
refs = defaultdict(dict)
for r in spark.sql(f"SELECT contract_key, project_id, ref_key FROM {S}.contracts WHERE ref_key IS NOT NULL").collect():
    refs[r.project_id][r.ref_key] = r.contract_key

def run_batch(batch):
    """One ai_query call over up to BATCH_SIZE prompts (one per passage); validated results and the log are written
    before returning."""
    req = spark.createDataFrame([(c["chunk_id"], lx.combined_prompt(c)) for c in batch], "chunk_id string, prompt string")
    req.createOrReplaceTempView("extraction_requests")
    replies = spark.sql(f"""
      SELECT chunk_id,
             ai_query('{LLM}', prompt,
                      modelParameters => named_struct('temperature', 0.0, 'max_tokens', 1500),
                      failOnError => false) AS r
      FROM extraction_requests""").select("chunk_id", "r.result", "r.errorMessage").collect()
    lessons, events, log_rows, rejected, errors = [], [], [], Counter(), 0
    now = datetime.now(timezone.utc)
    for r in replies:
        if r.errorMessage or r.result is None:
            errors += 1          # not logged -> retried next run
            continue
        c = by_id[r.chunk_id]
        kept_l, rej_l = lx.validate(r.result, c, "lessons")
        kept_e, rej_e = lx.validate(r.result, c, "events")
        rejected.update(rej_l)
        rejected.update(rej_e)
        for k in kept_l + kept_e:
            k["text_hash"] = c["text_hash"]
        for k in kept_e:
            rk = keys.ref_key(k.get("contract_reference"))
            k["matched_contract_key"] = refs.get(k["project_id"], {}).get(rk) if rk and len(rk) >= 4 else None
        lessons += kept_l
        events += kept_e
        log_rows.append((r.chunk_id, c["text_hash"], lx.KIND, len(kept_l) + len(kept_e),
                         json.dumps({"lessons": rej_l, "events": rej_e}), now))
    # Idempotent: drop any earlier rows for this batch's processed chunks first (e.g. a run that saved results but
    # stopped before writing the log), so re-processing a chunk never duplicates its lessons or events.
    ids = sorted({row[0] for row in log_rows})
    if ids:
        for table in (f"{G}.lessons", f"{S}.contract_events"):
            spark.sql(f"DELETE FROM {table} WHERE chunk_id IN ({', '.join(repr(x) for x in ids)})")
    if lessons:
        spark.createDataFrame([{k: l.get(k) for k in LESSON_COLS} for l in lessons], LESSON_SCHEMA) \
             .write.mode("append").saveAsTable(f"{G}.lessons")
    if events:
        spark.createDataFrame([{k: e.get(k) for k in EVENT_COLS} for e in events], EVENT_SCHEMA) \
             .write.mode("append").saveAsTable(f"{S}.contract_events")
    if log_rows:           # written LAST: a chunk counts as done only once its results are saved
        spark.createDataFrame(log_rows, "chunk_id string, text_hash string, kind string, n_kept int, rejected string, "
                              "processed_at timestamp").write.mode("append").saveAsTable(f"{O}.extraction_log")
    return {"calls": len(replies), "errors": errors, "lessons": len(lessons), "events": len(events),
            "matched": sum(1 for e in events if e.get("matched_contract_key")), "rejected": rejected}

by_id = {c["chunk_id"]: c for c in chunks}
totals = Counter()
n_batches = -(-len(todo) // BATCH_SIZE)
for i, batch in enumerate(lx.batches(todo, BATCH_SIZE), 1):
    res = run_batch(batch)
    totals.update({k: v for k, v in res.items() if k != "rejected"})
    totals.update({f"rejected_{k}": v for k, v in res["rejected"].items()})
    print(f"batch {i}/{n_batches}: {res['calls']} calls, {res['errors']} errors, {res['lessons']} lessons, "
          f"{res['events']} events kept (saved)")
report["llm"] = {"calls_this_run": totals["calls"], "errors_retry_next_run": totals["errors"],
                 "lessons_kept": totals["lessons"], "events_kept": totals["events"],
                 "events_matched_to_contract": totals["matched"],
                 "items_rejected": {k[9:]: v for k, v in totals.items() if k.startswith("rejected_")}}
print(json.dumps(report["llm"], indent=2))

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
