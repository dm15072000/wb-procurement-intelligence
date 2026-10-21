# Databricks notebook source
# MAGIC %md
# MAGIC # 20 — Documents: metadata → PDFs → parse once → section-aware chunks (Stage 3)
# MAGIC
# MAGIC **Scope (CLAUDE.md):** only the **16 demo-slice projects** (plus the latest CPF of each of the 5 countries).
# MAGIC Models train on all road projects, but parsing and embeddings stay on the slice.
# MAGIC
# MAGIC **Steps**
# MAGIC 1. List each project's documents from Documents & Reports: PAD, ISRs, ICR, ICR Review (IEG), PPAR, Project Papers
# MAGIC    (restructurings, and a fallback where no PAD is typed as such [Stage 2: 9 of 60 projects]), the latest
# MAGIC    Procurement Plan.
# MAGIC 2. Download the PDFs to the volume (skips files already there).
# MAGIC 3. Parse with `ai_parse_document` **once**, in small resumable batches, and **store** the result in
# MAGIC    `docs.parsed`. Parsing is not fully deterministic (`docs/05_parser_benchmark.md`), so everything downstream
# MAGIC    reads the stored copy and citations always point to the same text. Procurement Plans reuse the parse stored by
# MAGIC    notebook 02.
# MAGIC 4. Chunk by section (`src/chunking/sections.py`): boilerplate dropped, tables whole, heading path kept.
# MAGIC 5. Procurement Plan **labels**: cancellation / re-bid flags from status and description only, **no dates**
# MAGIC    (decision 2026-10-21).
# MAGIC
# MAGIC **Quota:** step 3 uses `ai_parse_document`. Documents over `MAX_PAGES` are skipped and listed. Re-running continues
# MAGIC where it stopped.

# COMMAND ----------

# MAGIC %pip install pypdf==5.1.0 --quiet

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os, sys, json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
sys.path.append(os.path.abspath(".."))
import importlib
import src.ingest.http as http
import src.chunking.sections as cs
import src.entity_resolution.keys as keys
for m in (http, cs, keys):
    importlib.reload(m)

CATALOG = "procurement"
D = f"{CATALOG}.docs"
PDF_DIR = f"/Volumes/{CATALOG}/bronze/raw_files/documents"
PLAN_PARSE = f"/Volumes/{CATALOG}/bronze/raw_files/procplan_test/parsed_raw.json"
os.makedirs(PDF_DIR, exist_ok=True)
MAX_PAGES = 400
BATCH = 15   # documents per ai_parse_document call; small so a failure loses little

slice_rows = spark.table(f"{CATALOG}.silver.projects").where("in_slice").select("project_id", "country_iso3_list").collect()
SLICE = sorted(r.project_id for r in slice_rows)
countries = spark.table(f"{CATALOG}.silver.countries").collect()
iso3_name = {c.iso3: c.name for c in countries}
SLICE_COUNTRIES = sorted({iso3_name[i] for r in slice_rows for i in r.country_iso3_list if i in iso3_name})
print(len(SLICE), "slice projects;", SLICE_COUNTRIES)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Document metadata

# COMMAND ----------

KEEP = {"Project Appraisal Document", "Implementation Status and Results Report",
        "Implementation Completion and Results Report", "Implementation Completion Report Review",
        "Project Performance Assessment Report", "Project Paper", "Procurement Plan"}
FL = "id,docty,docdt,pdfurl,display_title,lang,repnb"

def meta(v, project_id):
    return {"doc_id": str(v.get("id")), "project_id": project_id, "doc_type": v.get("docty"),
            "doc_date": keys.parse_date((v.get("docdt") or "")[:10]), "pdf_url": v.get("pdfurl"),
            "title": re.sub(r"\s+", " ", v.get("display_title") or "").strip(), "language": v.get("lang"),
            "report_no": v.get("repnb")}

docs = []
for pid in SLICE:
    rows = []
    for batch in http.search_pages("wds", {"proid": pid, "fl": FL}, list_key="documents", pause=0.7):
        rows += [meta(v, pid) for v in batch if v.get("docty") in KEEP and v.get("pdfurl")]
    plans = [r for r in rows if r["doc_type"] == "Procurement Plan"]
    latest_plan = max(plans, key=lambda r: r["doc_date"] or datetime.min.date(), default=None)
    rows = [r for r in rows if r["doc_type"] != "Procurement Plan"] + ([latest_plan] if latest_plan else [])
    docs += rows

for country in SLICE_COUNTRIES:   # latest Country Partnership Framework per country (policy context)
    cpf = []
    for batch in http.search_pages("wds", {"docty_exact": "Country Partnership Framework", "count_exact": country, "fl": FL},
                                   list_key="documents", pause=0.7):
        cpf += [meta(v, None) for v in batch if v.get("pdfurl")]
    if cpf:
        docs.append(dict(max(cpf, key=lambda r: r["doc_date"] or datetime.min.date()), country=country))

docs = list({d["doc_id"]: d for d in docs}.values())   # de-duplicate (a doc can list several projects)
for d in docs:
    d.setdefault("country", None)
spark.createDataFrame(docs, "doc_id string, project_id string, doc_type string, doc_date date, pdf_url string, "
                      "title string, language string, report_no string, country string") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{D}.documents")
print(Counter(d["doc_type"] for d in docs))
no_pad = [p for p in SLICE if not any(d["project_id"] == p and d["doc_type"] == "Project Appraisal Document" for d in docs)]
print("slice projects with no document typed PAD (Project Papers kept as fallback):", no_pad)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Download PDFs (resumable) and count pages

# COMMAND ----------

from pypdf import PdfReader
pages, failed = {}, {}
for d in docs:
    path = f"{PDF_DIR}/{d['doc_id']}.pdf"
    try:
        if not os.path.exists(path):
            data = http.get(d["pdf_url"], as_json=False, pause=1.0)
            with open(path + ".tmp", "wb") as fh:
                fh.write(data)
            os.replace(path + ".tmp", path)
        pages[d["doc_id"]] = len(PdfReader(path).pages)
    except Exception as e:
        failed[d["doc_id"]] = f"{type(e).__name__}: {str(e)[:120]}"
print(f"downloaded {len(pages)} | failed {len(failed)} | total pages {sum(pages.values()):,}")
print("failures:", failed)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Parse once with `ai_parse_document`, store in `docs.parsed`
# MAGIC Procurement Plans are not parsed again: notebook 02 stored their parse, which is reused. Everything else is
# MAGIC parsed in batches of `BATCH`; each batch is appended immediately, so a re-run only parses what's missing.

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {D}.parsed (doc_id STRING, parsed STRING, parser STRING, parsed_at TIMESTAMP)""")
already = {r.doc_id for r in spark.table(f"{D}.parsed").select("doc_id").collect()}

# 3a. Reuse stored Procurement Plan parses (keyed by project in notebook 02).
if os.path.exists(PLAN_PARSE):
    stored = json.load(open(PLAN_PARSE))
    reuse = [{"doc_id": d["doc_id"], "parsed": stored[d["project_id"]], "parser": "ai_parse_document (stored by notebook 02)",
              "parsed_at": datetime.now(timezone.utc)}
             for d in docs if d["doc_type"] == "Procurement Plan" and d["project_id"] in stored and d["doc_id"] not in already]
    if reuse:
        spark.createDataFrame(reuse, "doc_id string, parsed string, parser string, parsed_at timestamp") \
             .write.mode("append").saveAsTable(f"{D}.parsed")
    print("reused stored plan parses:", len(reuse))
    already |= {r["doc_id"] for r in reuse}

# 3b. Parse the rest. Order: ICR, ICR Review, PAD, Project Paper, ISR, CPF (most valuable first, in case quota runs out).
PRIORITY = {"Implementation Completion and Results Report": 0, "Implementation Completion Report Review": 1,
            "Project Appraisal Document": 2, "Project Performance Assessment Report": 3, "Project Paper": 4,
            "Implementation Status and Results Report": 5}
todo = sorted([d for d in docs if d["doc_id"] in pages and d["doc_id"] not in already and pages[d["doc_id"]] <= MAX_PAGES],
              key=lambda d: (PRIORITY.get(d["doc_type"], 9), d["doc_id"]))
skipped = {d["doc_id"]: pages[d["doc_id"]] for d in docs if d["doc_id"] in pages and pages[d["doc_id"]] > MAX_PAGES}
print(f"to parse: {len(todo)} docs, {sum(pages[d['doc_id']] for d in todo):,} pages | skipped (> {MAX_PAGES} pages): {skipped}")

from pyspark.sql import functions as F
import time
for i in range(0, len(todo), BATCH):
    batch = [f"{PDF_DIR}/{d['doc_id']}.pdf" for d in todo[i:i + BATCH]]
    t0 = time.perf_counter()
    (spark.read.format("binaryFile").load(batch)
          .select(F.regexp_extract("path", r"([^/]+)\.pdf$", 1).alias("doc_id"),
                  F.expr("to_json(ai_parse_document(content))").alias("parsed"))
          .withColumn("parser", F.lit("ai_parse_document"))
          .withColumn("parsed_at", F.current_timestamp())
          .write.mode("append").saveAsTable(f"{D}.parsed"))
    print(f"batch {i // BATCH + 1}: {len(batch)} docs in {time.perf_counter() - t0:.0f}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3c. Check the parse output shape (once)
# MAGIC The chunker reads `document.elements` (falling back to a structure walk). This prints the top-level keys and the
# MAGIC first elements of one parsed ICR, so we can see the real shape before trusting the chunks.

# COMMAND ----------

icr_ids = [d["doc_id"] for d in docs if d["doc_type"] == "Implementation Completion and Results Report"]
probe = spark.table(f"{D}.parsed").where(F.col("doc_id").isin(icr_ids)).first()
assert probe is not None, "no ICR parsed yet: check the batch output above"
pj = json.loads(probe.parsed)
print("top-level keys:", list(pj.keys()), "| document keys:", list((pj.get("document") or {}).keys()))
els = cs.elements_from_parse(pj)
print("elements:", len(els), Counter(e["type"] for e in els).most_common(), "| with page:", sum(e["page"] is not None for e in els))
print(json.dumps((pj.get("document") or {}).get("elements", [])[:3], indent=1)[:1500])
errors = Counter()
for r in spark.table(f"{D}.parsed").select("doc_id", F.get_json_object("parsed", "$.error_status").alias("err")).collect():
    errors["error" if r.err not in (None, "null", "[]") else "ok"] += 1
print("parse status:", dict(errors))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Section-aware chunks → `docs.chunks` (and `docs.sections`)
# MAGIC Every chunk keeps: its section path, its pages, and a context header `[doc type | project | date] path`. The
# MAGIC header is used at embedding time (Stage 5) and in citations.

# COMMAND ----------

meta_by_id = {d["doc_id"]: d for d in docs}
chunks = []
for r in spark.table(f"{D}.parsed").select("doc_id", "parsed").toLocalIterator():
    m = meta_by_id.get(r.doc_id)
    if not m or not r.parsed:
        continue
    cm = {"doc_id": m["doc_id"], "project_id": m["project_id"] or m.get("country"), "doc_type": m["doc_type"],
          "doc_date": str(m["doc_date"])}
    for ch in cs.chunk_document(cs.elements_from_parse(r.parsed), cm):
        ch.update({"project_id": m["project_id"], "doc_type": m["doc_type"], "doc_date": m["doc_date"]})
        chunks.append(ch)

spark.createDataFrame(chunks, "chunk_id string, doc_id string, seq int, chunk_type string, section_path string, "
                      "text string, context_header string, page_start int, page_end int, n_tokens int, "
                      "project_id string, doc_type string, doc_date date") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{D}.chunks")
spark.sql(f"""CREATE OR REPLACE TABLE {D}.sections AS
              SELECT doc_id, section_path, min(seq) AS first_chunk_seq, count(*) AS n_chunks,
                     min(page_start) AS page_start, max(page_end) AS page_end
              FROM {D}.chunks GROUP BY doc_id, section_path""")

tok = sorted(c["n_tokens"] for c in chunks)
chunk_report = {
    "docs_chunked": len({c["doc_id"] for c in chunks}), "chunks": len(chunks),
    "by_type": dict(Counter(c["chunk_type"] for c in chunks)),
    "tokens_p50_p90_max": [tok[len(tok) // 2], tok[int(len(tok) * 0.9)], tok[-1]] if tok else None,
    "chunks_without_section": sum(1 for c in chunks if not c["section_path"]),
    "by_doc_type": dict(Counter(c["doc_type"] for c in chunks)),
}
print(json.dumps(chunk_report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Spot check: 3 chunks from an ICR's lessons section, and 1 table chunk

# COMMAND ----------

for row in spark.sql(f"""SELECT context_header, page_start, substr(text, 1, 600) AS text FROM {D}.chunks
                         WHERE doc_type = 'Implementation Completion and Results Report'
                           AND lower(section_path) LIKE '%lesson%' LIMIT 3""").collect():
    print(row.context_header, "| p.", row.page_start, "\n", row.text, "\n---")
for row in spark.sql(f"SELECT context_header, substr(text, 1, 600) AS text FROM {D}.chunks WHERE chunk_type = 'table' LIMIT 1").collect():
    print(row.context_header, "\n", row.text)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Procurement Plan labels: status and description only, NO dates
# MAGIC Rebuilt from the stored plan parses in `docs.parsed` with the same checker that passed the gate in notebook 02
# MAGIC (`src/procplan/check.py`, 74.8% clean). Only *clean* rows are kept. Flags: `is_cancelled` (status Canceled /
# MAGIC Cancelled / Terminated) and `mentions_rebid` (description says rebid / re-tender / re-advertise). Rows whose reference
# MAGIC is a header label ("Activity Reference No.") are excluded: that is the merged-header defect found in the
# MAGIC diagnostics. **No date fields are read or stored.**

# COMMAND ----------

import src.procplan.check as ck
importlib.reload(ck)
REBID = re.compile(r"\bre-?bid(ding)?\b|\bre-?tender(ing)?\b|\bre-?advertis", re.I)
plan_ids = {d["doc_id"]: d["project_id"] for d in docs if d["doc_type"] == "Procurement Plan"}
labels, header_rows, detected = [], 0, 0
for r in spark.table(f"{D}.parsed").where(F.col("doc_id").isin(list(plan_ids))).select("doc_id", "parsed").collect():
    for el in cs.elements_from_parse(r.parsed):
        if el["type"] != "table":
            continue
        for row in ck.html_grid(el["content"]):
            if not any((c or "").strip() in ("Prior", "Post") for c in row):
                continue
            detected += 1
            rec = ck.check_row(row)
            if rec["reason"] != "clean":
                continue
            if rec["ref"].lower().startswith("activity reference"):
                header_rows += 1
                continue
            labels.append({"project_id": plan_ids[r.doc_id], "activity_ref": rec["ref"], "ref_key": keys.ref_key(rec["ref"]),
                           "review_type": rec["review"], "method": rec["method"], "status": rec["status"],
                           "description": rec["description"],
                           "is_cancelled": rec["status"].strip().lower() in ("canceled", "cancelled", "terminated"),
                           "mentions_rebid": bool(REBID.search(rec["description"] or ""))})
spark.createDataFrame(labels, "project_id string, activity_ref string, ref_key string, review_type string, method string, "
                      "status string, description string, is_cancelled boolean, mentions_rebid boolean")      .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.silver.plan_activity_labels")
label_report = {"plan_rows_detected": detected, "clean_used": len(labels), "excluded_header_rows": header_rows,
                "cancelled": sum(l["is_cancelled"] for l in labels), "mentions_rebid": sum(l["mentions_rebid"] for l in labels),
                "by_project": dict(Counter(l["project_id"] for l in labels))}
print(json.dumps(label_report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Summary: copy back for checkpoint (c)

# COMMAND ----------

print(json.dumps({"documents": dict(Counter(d["doc_type"] for d in docs)), "download_failed": failed,
                  "skipped_too_long": skipped, "parse_status": dict(errors), "chunks": chunk_report,
                  "plan_labels": label_report, "slice_projects_without_PAD": no_pad}, indent=2, default=str))
