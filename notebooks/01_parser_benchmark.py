# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 01 — PDF parser benchmark: `ai_parse_document` vs `pdfplumber` (decision D6)
# MAGIC
# MAGIC **What this notebook does:** downloads 3 real ICRs from our demo slice and parses each one two ways:
# MAGIC 1. Databricks' built-in SQL function `ai_parse_document`
# MAGIC 2. Open-source `pdfplumber` (tables + layout), with `pypdf` for fast plain text
# MAGIC
# MAGIC It then scores both on the three criteria you set: **table fidelity**, **heading preservation**, **speed**.
# MAGIC
# MAGIC **Why:** ICRs are 50–150 page reports full of tables (ratings, key dates, costs) and numbered sections. Our chunking is
# MAGIC section-aware, so the parser must keep headings and must not shred tables. We pick the parser on measured evidence.
# MAGIC
# MAGIC **Side effects (all approved):** creates catalog `procurement` (falls back to `workspace` if not allowed), schema
# MAGIC `bronze` and volume `raw_files`, and installs `pdfplumber` + `pypdf` on this notebook's serverless environment.
# MAGIC `ai_parse_document` uses Free Edition AI-function quota on 3 documents only.

# COMMAND ----------

# MAGIC %pip install pdfplumber==0.11.4 pypdf==5.1.0 --quiet

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Storage: catalog, schema, volume
# MAGIC We try the approved catalog `procurement` first. If Free Edition refuses, we fall back to catalog `workspace`
# MAGIC (decision D3) and print which one was used, so CLAUDE.md can be updated.

# COMMAND ----------

CATALOG = "procurement"
try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
except Exception as e:
    print(f"Could not create catalog '{CATALOG}' ({type(e).__name__}); falling back to 'workspace'.\n{str(e)[:300]}")
    CATALOG = "workspace"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.bronze")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.bronze.raw_files")
VOL = f"/Volumes/{CATALOG}/bronze/raw_files/benchmark_icr"
dbutils.fs.mkdirs(VOL)
print("Catalog used:", CATALOG, "| files go to:", VOL)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Download 3 ICRs
# MAGIC Chosen for variety: two recent-template ICRs (India 2024, Sri Lanka 2024) and one older layout (China 2021).
# MAGIC URLs come from the Documents & Reports API (verified in Stage 2). The download is polite: explicit User-Agent,
# MAGIC pause between files, exponential backoff on HTTP 429/5xx (CLAUDE.md rule).

# COMMAND ----------

import random, time, urllib.error, urllib.request

ICRS = {
    "IND_P147864": "http://documents.worldbank.org/curated/en/099010225103533269/pdf/BOSIB15805b6860c219c0c15555a91737dd.pdf",
    "LKA_P132833": "http://documents.worldbank.org/curated/en/099062024101516722/pdf/BOSIB14ec133ff09f198ce12d86a1cee951.pdf",
    "CHN_P129401": "http://documents.worldbank.org/curated/en/997951624712258993/pdf/China-Guiyang-Rural-Roads-Project.pdf",
}
UA = "Mozilla/5.0 (procurement-intelligence-research)"

def polite_get(url, max_tries=6, base=5, pause=2.0):
    """Download bytes with exponential backoff; honours Retry-After on 429."""
    for attempt in range(max_tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=180) as r:
                data = r.read()
            time.sleep(pause)
            return data
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            wait = float(e.headers.get("Retry-After") or base * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            wait = base * 2 ** attempt
        time.sleep(min(wait, 300) + random.uniform(0, 1))
    raise RuntimeError(f"gave up: {url}")

for name, url in ICRS.items():
    path = f"{VOL}/{name}.pdf"
    data = polite_get(url)
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"{name}: {len(data)/1e6:.1f} MB")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Reference for scoring headings
# MAGIC Current-template ICRs use five standard top-level sections **[assumption: checked by this run]**. Heading
# MAGIC preservation = share of these five that a parser returns *as a heading* (not just somewhere in the text).

# COMMAND ----------

import re

EXPECTED_HEADINGS = [
    "project context and development objectives",
    "outcome",
    "key factors that affected implementation and outcome",
    "bank performance, compliance issues, and risk to development outcome",
    "lessons and recommendations",
]

def norm(s):
    return re.sub(r"[^a-z ,]", "", re.sub(r"\s+", " ", (s or "").lower())).strip()

def heading_recall(headings):
    """Share of expected sections found among a parser's headings (a heading counts if it contains the phrase)."""
    hs = [norm(h) for h in headings]
    found = [e for e in EXPECTED_HEADINGS if any(e in h and len(h) < len(e) + 25 for h in hs)]
    return round(len(found) / len(EXPECTED_HEADINGS), 2), found

def table_quality(rows):
    """A table is 'well-formed' if it has >=2 rows, >=2 columns and under half of its cells empty."""
    if not rows or len(rows) < 2:
        return False
    ncol = max(len(r) for r in rows)
    cells = [c for r in rows for c in r]
    empty = sum(1 for c in cells if c is None or str(c).strip() == "")
    return ncol >= 2 and empty / max(len(cells), 1) < 0.5

def key_dates_found(tables_as_text):
    """ICR datasheets have a 'Key Dates' table with Approval / Effectiveness / Closing rows. True if one table holds all three."""
    return any(all(k in t.lower() for k in ("approval", "effectiveness", "closing")) for t in tables_as_text)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Parser A — `ai_parse_document`
# MAGIC Runs the built-in function over the binary PDFs. We time the whole call and then walk the returned elements,
# MAGIC counting headings (`title` / `section_header`) and tables. The output schema is read defensively, because element
# MAGIC type names may differ by version.

# COMMAND ----------

import json, html as htmlmod

t0 = time.perf_counter()
parsed = spark.sql(f"""
    SELECT path, to_json(ai_parse_document(content)) AS doc
    FROM READ_FILES('{VOL}', format => 'binaryFile')
""").collect()
ai_seconds_total = time.perf_counter() - t0

def walk_elements(obj):
    """Yield every dict that has a 'type' key, wherever it sits in the returned JSON."""
    if isinstance(obj, dict):
        if "type" in obj:
            yield obj
        for v in obj.values():
            yield from walk_elements(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_elements(v)

def html_table_rows(s):
    """Very small HTML table reader: rows of cell texts."""
    rows = []
    for tr in re.findall(r"<tr.*?>(.*?)</tr>", s or "", flags=re.S | re.I):
        rows.append([htmlmod.unescape(re.sub(r"<.*?>", "", c)).strip()
                     for c in re.findall(r"<t[dh].*?>(.*?)</t[dh]>", tr, flags=re.S | re.I)])
    return rows

ai_results, ai_samples = {}, {}
for r in parsed:
    name = r.path.split("/")[-1].replace(".pdf", "")
    d = json.loads(r.doc)
    els = list(walk_elements(d))
    types = {}
    for e in els:
        types[e["type"]] = types.get(e["type"], 0) + 1
    heads = [e.get("content", "") for e in els if e["type"] in ("title", "section_header")]
    tables = [e.get("content", "") for e in els if e["type"] == "table"]
    table_rows = [html_table_rows(t) for t in tables]
    pages = len(d.get("document", {}).get("pages", []) or [])
    rec, found = heading_recall(heads)
    ai_results[name] = {
        "pages": pages, "element_types": types, "headings": len(heads), "heading_recall": rec,
        "tables": len(tables), "tables_well_formed": sum(table_quality(t) for t in table_rows),
        "key_dates_table": key_dates_found(tables), "error": d.get("error_status"),
    }
    ai_samples[name] = {"headings": heads[:25], "first_tables": tables[:3]}
ai_results["_seconds_total_3_docs"] = round(ai_seconds_total, 1)
print(json.dumps(ai_results, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Parser B — `pdfplumber` (+ `pypdf` for plain text)
# MAGIC `pypdf` gives plain text quickly. `pdfplumber` gives tables and per-character font sizes. PDFs carry no "this is a
# MAGIC heading" tag, so we infer headings from layout: lines in a noticeably larger or bold font, short, mostly capitals.

# COMMAND ----------

import statistics
import pdfplumber
from pypdf import PdfReader

def plumber_headings(page):
    """Infer heading lines on one page from font size / boldness."""
    words = page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False)
    if not words:
        return []
    body = statistics.median(w["size"] for w in words)
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"]), []).append(w)
    out = []
    for _, ws in sorted(lines.items()):
        text = " ".join(w["text"] for w in ws).strip()
        size = max(w["size"] for w in ws)
        bold = all("bold" in w["fontname"].lower() for w in ws)
        letters = [c for c in text if c.isalpha()]
        upper = sum(c.isupper() for c in letters) / max(len(letters), 1)
        if 3 < len(text) < 120 and (size >= body * 1.15 or (bold and upper > 0.6)):
            out.append(text)
    return out

pl_results, pl_samples = {}, {}
for name in ICRS:
    path = f"{VOL}/{name}.pdf"
    t0 = time.perf_counter()
    text_chars = sum(len(p.extract_text() or "") for p in PdfReader(path).pages)
    pypdf_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    heads, tables = [], []
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        for p in pdf.pages:
            heads += plumber_headings(p)
            tables += p.extract_tables()
    plumber_s = time.perf_counter() - t0

    table_text = ["\n".join(" | ".join(str(c or "") for c in row) for row in t) for t in tables]
    rec, found = heading_recall(heads)
    pl_results[name] = {
        "pages": pages, "pypdf_chars": text_chars, "pypdf_seconds": round(pypdf_s, 1),
        "pdfplumber_seconds": round(plumber_s, 1), "headings": len(heads), "heading_recall": rec,
        "tables": len(tables), "tables_well_formed": sum(table_quality(t) for t in tables),
        "key_dates_table": key_dates_found(table_text),
    }
    pl_samples[name] = {"headings": heads[:25], "first_tables": table_text[:3]}
print(json.dumps(pl_results, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Side-by-side samples for a manual table check
# MAGIC Automatic scores can't judge whether a table's cells are *correct*. This prints the first headings and first table
# MAGIC from each parser for one ICR, so we can eyeball fidelity against the PDF.

# COMMAND ----------

name = "IND_P147864"
print("=== ai_parse_document headings ===\n", "\n".join(ai_samples.get(name, {}).get("headings", [])[:15]))
print("\n=== pdfplumber headings ===\n", "\n".join(pl_samples[name]["headings"][:15]))
print("\n=== ai_parse_document first table (HTML) ===\n", (ai_samples.get(name, {}).get("first_tables") or [""])[0][:1500])
print("\n=== pdfplumber first table ===\n", (pl_samples[name]["first_tables"] or [""])[0][:1500])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Summary — copy this output back
# MAGIC Paste the block below (and the cell 6 output) into the chat so the results can be recorded as **measured** in
# MAGIC `docs/05_parser_benchmark.md`.

# COMMAND ----------

print(json.dumps({"catalog_used": CATALOG, "ai_parse_document": ai_results, "pdfplumber": pl_results}, indent=2, default=str))
