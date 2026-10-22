# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02 — Procurement Plan parse test (checkpoint b follow-up)
# MAGIC
# MAGIC **What this notebook does:** takes the *latest* Procurement Plan for each of the 16 demo-slice projects, parses its
# MAGIC activity tables with `ai_parse_document` (the parser chosen in `docs/05_parser_benchmark.md`), and measures:
# MAGIC 1. **Parse rate:** what share of activities parse cleanly. **Gate: ~70%.** Below that, plans are dropped.
# MAGIC 2. **(a)** whether plan dates give a cleaner tender-duration measure than the notice→signing join (H1).
# MAGIC 3. **(b)** how much of the FY2017–FY2019 contract-data gap the plans cover.
# MAGIC
# MAGIC **Why:** plans carry planned *and actual* dates for every procurement step, which could improve the duration target
# MAGIC and partly fill the gap. But we only use them in a model if the numbers say they're reliable.
# MAGIC
# MAGIC **Why not the .txt version:** Stage 2 showed the text export wraps dates and amounts across lines
# MAGIC (`2016-09-0` / `6`) and cuts off the rightmost column, so it's not a fair test of the plans themselves.
# MAGIC
# MAGIC **Side effects:** writes PDFs and one JSON result file to the approved volume. Nothing goes into any model.
# MAGIC Uses `ai_parse_document` quota; documents over 300 pages are skipped and recorded.

# COMMAND ----------

# MAGIC %pip install pypdf==5.1.0 --quiet

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Setup: storage path and a polite HTTP client
# MAGIC Same rules as every other ingestion step: explicit User-Agent, pause between calls, exponential backoff that
# MAGIC honours HTTP 429 `Retry-After`.

# COMMAND ----------

import json, random, re, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime

CATALOG = "procurement"   # created by notebook 01 (verified: "catalog_used": "procurement")
VOL = f"/Volumes/{CATALOG}/bronze/raw_files/procplan_test"
dbutils.fs.mkdirs(VOL)
UA = "Mozilla/5.0 (procurement-intelligence-research)"

def polite_get(url, max_tries=7, base=5, pause=1.5, as_json=False):
    for attempt in range(max_tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
            time.sleep(pause)
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise
            wait = float(e.headers.get("Retry-After") or base * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            wait = base * 2 ** attempt
        time.sleep(min(wait, 300) + random.uniform(0, 1))
    raise RuntimeError(f"gave up: {url}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Find and download the latest plan per project
# MAGIC The 16 slice projects are the ones recorded in CLAUDE.md / `docs/04_data_sources.md`. "Latest" = most recent
# MAGIC document date in Documents & Reports.

# COMMAND ----------

SLICE = ["P147864", "P163239", "P157054", "P160505", "P155522", "P151449", "P153548", "P132833",
         "P144726", "P143751", "P132775", "P132621", "P132982", "P145778", "P129401", "P121185"]

from pypdf import PdfReader
plans = {}
for pid in SLICE:
    d = polite_get(f"https://search.worldbank.org/api/v2/wds?format=json&rows=200&proid={pid}"
                   f"&docty_exact=Procurement%20Plan&fl=id,docdt,repnb,pdfurl", as_json=True, pause=0.7)
    docs = [v for k, v in d["documents"].items() if k != "facets"]
    latest = max(docs, key=lambda x: x.get("docdt", ""))
    path = f"{VOL}/{pid}.pdf"
    with open(path, "wb") as fh:
        fh.write(polite_get(latest["pdfurl"]))
    pages = len(PdfReader(path).pages)
    plans[pid] = {"doc_id": latest["id"], "docdt": latest["docdt"][:10], "repnb": latest.get("repnb"), "pages": pages}
    print(pid, plans[pid])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Parse with `ai_parse_document`
# MAGIC Documents over 300 pages are skipped (quota protection) and recorded as "not parsed". A skipped plan has no
# MAGIC activities to count, so the summary also reports **plan coverage** (plans that yielded activities ÷ 16). That way a
# MAGIC skip can't make the activity parse rate look better unnoticed.

# COMMAND ----------

MAX_PAGES = 300
to_parse = [pid for pid, p in plans.items() if p["pages"] <= MAX_PAGES]
for pid, p in plans.items():
    if pid not in to_parse:
        p["skipped"] = f"{p['pages']} pages > {MAX_PAGES}"
paths = ", ".join(f"'{VOL}/{pid}.pdf'" for pid in to_parse)

t0 = time.perf_counter()
rows = spark.sql(f"""
    SELECT path, to_json(ai_parse_document(content)) AS doc
    FROM READ_FILES('{VOL}/*.pdf', format => 'binaryFile')
    WHERE path IN ({", ".join(f"'dbfs:{VOL}/{pid}.pdf'" for pid in to_parse)})
       OR path IN ({paths})
""").collect()
print(f"parsed {len(rows)} plans in {time.perf_counter() - t0:.0f}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Turn HTML tables into activity records
# MAGIC Each plan has one table per category (works, goods, consultants…), often split across pages and with headers over
# MAGIC several rows. We expand merged cells (`colspan` / `rowspan`), join the header rows into one name per column, carry
# MAGIC the header to continuation tables, and keep the rows that look like activities (they have a Prior/Post review type).

# COMMAND ----------

import html as htmlmod

def walk(obj):
    if isinstance(obj, dict):
        if "type" in obj:
            yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def html_grid(s):
    """HTML table -> list of rows, expanding colspan/rowspan so every row has aligned columns."""
    grid, pending = [], {}
    for tr in re.findall(r"<tr.*?>(.*?)</tr>", s or "", flags=re.S | re.I):
        row, col = [], 0
        cells = re.findall(r"<t([dh])([^>]*)>(.*?)</t[dh]>", tr, flags=re.S | re.I)
        it = iter(cells)
        while True:
            while col in pending:
                txt, left = pending[col]
                row.append(txt)
                pending[col] = (txt, left - 1) if left > 1 else None
                if pending[col] is None:
                    del pending[col]
                col += 1
            c = next(it, None)
            if c is None:
                break
            attrs, txt = c[1], htmlmod.unescape(re.sub(r"<.*?>", " ", c[2]))
            txt = re.sub(r"\s+", " ", txt).strip()
            cs = int((re.search(r'colspan="?(\d+)', attrs) or [0, 1])[1])
            rs = int((re.search(r'rowspan="?(\d+)', attrs) or [0, 1])[1])
            for _ in range(cs):
                if rs > 1:
                    pending[col] = (txt, rs - 1)
                row.append(txt)
                col += 1
        grid.append(row)
    return grid

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
def is_activity(row):
    return any(c in ("Prior", "Post") for c in row)

def find_col(names, *need, exclude=()):
    for i, n in enumerate(names):
        if all(k in n for k in need) and not any(x in n for x in exclude):
            return i
    return None

def valid_iso(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def clean_reason(rec):
    """Single source of truth: returns 'clean' or the FIRST failed rule. Used both to decide clean and to explain failures,
    so the two can never disagree (run 1 bug: the explanation skipped the review-type rule, giving 'ok': 14 with 0 clean)."""
    if not rec["ref"]:
        return "no ref"
    if rec["review"] not in ("Prior", "Post"):
        return "no review type"
    if not rec["method"]:
        return "no method"
    if not rec["status"]:
        return "no status"
    if not rec["n_dates"]:
        return "no dates"
    if rec["bad_dates"]:
        return "bad date"
    return "clean"

activities = []
for r in rows:
    pid = r.path.split("/")[-1].replace(".pdf", "")
    doc = json.loads(r.doc)
    header = None
    for el in walk(doc):
        if el.get("type") != "table":
            continue
        grid = html_grid(el.get("content", ""))
        if not grid:
            continue
        first_act = next((i for i, row in enumerate(grid) if is_activity(row)), None)
        if first_act is None:
            continue
        if first_act > 0:
            ncol = max(len(x) for x in grid[:first_act])
            names = [" ".join((g[i] if i < len(g) else "") for g in grid[:first_act]).lower() for i in range(ncol)]
            header = names
        names = header or []
        if not names:
            continue
        c_rev = find_col(names, "review")
        c_meth = find_col(names, "method")
        c_stat = find_col(names, "status")
        date_cols = [i for i, n in enumerate(names) if "planned" in n or "actual" in n]
        c_sign = find_col(names, "signed", "actual") or find_col(names, "contract", "actual", exclude=("completion",))
        c_note = [i for i, n in enumerate(names) if "actual" in n and any(k in n for k in ("notice", "invitation", "expression of interest"))]
        for row in grid[first_act:]:
            if not is_activity(row):
                continue
            cell = lambda i: (row[i] if i is not None and i < len(row) else "").strip()
            dates = {names[i]: cell(i) for i in date_cols if cell(i)}
            bad_dates = [v for v in dates.values() if not (ISO.match(v) and valid_iso(v))]
            ref = cell(0).split(" / ")[0].strip()
            rec = {
                "pid": pid, "ref": ref, "review": cell(c_rev), "method": cell(c_meth), "status": cell(c_stat),
                "n_dates": len(dates), "bad_dates": bad_dates,
                "signed_actual": cell(c_sign) if c_sign is not None and ISO.match(cell(c_sign)) else None,
                "notice_actual": min([cell(i) for i in c_note if ISO.match(cell(i))], default=None),
                "description": cell(0),
            }
            rec["reason"] = clean_reason(rec)
            rec["clean"] = rec["reason"] == "clean"
            activities.append(rec)

print("activities detected:", len(activities))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Gate: parse rate
# MAGIC **Clean** = has a reference number, a Prior/Post review type, a method, a status, at least one date, and *every*
# MAGIC date is a valid full ISO date (no truncated or wrapped dates). Rate = clean ÷ detected. Plans that were skipped or
# MAGIC yielded no activity tables are listed separately, because they can't be in the denominator.

# COMMAND ----------

from collections import Counter, defaultdict
per_plan = defaultdict(lambda: [0, 0])
for a in activities:
    per_plan[a["pid"]][0] += 1
    per_plan[a["pid"]][1] += a["clean"]
detected = len(activities)
clean = sum(a["clean"] for a in activities)
gate = {
    "activities_detected": detected,
    "activities_clean": clean,
    "parse_rate": round(clean / detected, 3) if detected else 0.0,
    "passes_70pct_gate": bool(detected) and clean / detected >= 0.70,
    "plans_with_activities": sum(1 for p in SLICE if per_plan[p][0] > 0),
    "plan_coverage": round(sum(1 for p in SLICE if per_plan[p][0] > 0) / len(SLICE), 3),
    "plans_without_activities": [p for p in SLICE if per_plan[p][0] == 0],
    "plans_skipped": {p: v["skipped"] for p, v in plans.items() if "skipped" in v},
    "per_plan_detected_clean": {p: per_plan[p] for p in SLICE},
    "why_not_clean": Counter(a["reason"] for a in activities),
}
print(json.dumps(gate, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Test (a): is the plan duration cleaner than the notice→signing join?
# MAGIC **Plan duration** = actual contract signing − actual notice/invitation/REOI date, both taken from the plan.
# MAGIC **Join duration** = DS00005 signing date − earliest matching IFB/REOI notice (the H1 method), for the *same* 16
# MAGIC projects. We compare coverage, the share of negative durations, the share over 3 years, and medians, by review type.
# MAGIC Where a plan activity matches a DS00005 contract by reference number, we also check whether both sources agree
# MAGIC on the signing date (within 7 days).

# COMMAND ----------

norm = lambda s: re.sub(r"[^A-Z0-9]", "", (s or "").upper())
D = lambda s: datetime.strptime(s, "%Y-%m-%d").date()

def stats(durs):
    if not durs:
        return {"n": 0}
    s = sorted(durs)
    return {"n": len(s), "negative_pct": round(sum(d < 0 for d in s) / len(s), 3),
            "over_3y_pct": round(sum(d > 1095 for d in s) / len(s), 3), "median_days": s[len(s) // 2]}

plan_durs = defaultdict(list)
if gate["passes_70pct_gate"]:
    for a in activities:
        if a["clean"] and a["signed_actual"] and a["notice_actual"]:
            plan_durs[a["review"]].append((D(a["signed_actual"]) - D(a["notice_actual"])).days)

FONE = "https://datacatalogapi.worldbank.org/dexapps/fone/api/apiservice?datasetId=DS00005&resourceId=RS00005&type=json&top=1000"
join_durs, sign_agree = defaultdict(list), []
plan_by_ref = {(a["pid"], norm(a["ref"])): a for a in activities if a["clean"] and a["signed_actual"]}
for pid in SLICE:
    cons, skip = [], 0
    while True:
        b = polite_get(f"{FONE}&skip={skip}&filter=" + urllib.parse.quote(f"project_id='{pid}'"), as_json=True).get("data") or []
        cons += b
        if len(b) < 1000:
            break
        skip += 1000
    nots, os_ = [], 0
    while True:
        b = polite_get(f"https://search.worldbank.org/api/v2/procnotices?format=json&rows=500&os={os_}&project_id={pid}"
                       f"&fl=notice_type,noticedate,bid_reference_no", as_json=True, pause=0.7).get("procnotices") or []
        nots += b
        if len(b) < 500:
            break
        os_ += 500
    starts = defaultdict(list)
    for x in nots:
        if x.get("notice_type") in ("Invitation for Bids", "Request for Expression of Interest") and x.get("bid_reference_no"):
            starts[norm(x["bid_reference_no"])].append(datetime.strptime(x["noticedate"], "%d-%b-%Y").date())
    for c in {x["wb_contract_number"]: x for x in cons}.values():
        k = norm(c.get("borrower_contract_reference_number"))
        signed = datetime.strptime(c["contract_signing_date"], "%d-%b-%Y").date()
        if k in starts:
            join_durs[c.get("review_type") or "unknown"].append((signed - min(starts[k])).days)
        p = plan_by_ref.get((pid, k))
        if p:
            sign_agree.append(abs((D(p["signed_actual"]) - signed).days) <= 7)

test_a = {
    "plan_duration_by_review": {k: stats(v) for k, v in plan_durs.items()},
    "join_duration_by_review_same_16_projects": {k: stats(v) for k, v in join_durs.items()},
    "plan_vs_DS00005_matched_contracts": len(sign_agree),
    "signing_date_agrees_within_7d_pct": round(sum(sign_agree) / len(sign_agree), 3) if sign_agree else None,
} if gate["passes_70pct_gate"] else {"skipped": "plans failed the 70% parse gate"}
print(json.dumps(test_a, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Test (b): how much of the FY2017–FY2019 gap do the plans cover? (historical: written before DS04691 showed FY2017–19 contracts exist, partially covered)
# MAGIC World Bank FY2017–FY2019 = 1 July 2016 to 30 June 2019. Both contract datasets have **zero** rows in that window
# MAGIC (verified). We count clean plan activities with an *actual* signing date inside it.

# COMMAND ----------

GAP = (date(2016, 7, 1), date(2019, 6, 30))
if gate["passes_70pct_gate"]:
    signed = [a for a in activities if a["clean"] and a["signed_actual"]]
    in_gap = [a for a in signed if GAP[0] <= D(a["signed_actual"]) <= GAP[1]]
    test_b = {
        "clean_activities_with_actual_signing": len(signed),
        "signed_in_FY2017_FY2019": len(in_gap),
        "share_of_signed": round(len(in_gap) / len(signed), 3) if signed else None,
        "projects_with_gap_activities": sorted({a["pid"] for a in in_gap}),
        "by_review_type": dict(Counter(a["review"] for a in in_gap)),
    }
else:
    test_b = {"skipped": "plans failed the 70% parse gate"}
print(json.dumps(test_b, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Save raw results and print the summary to copy back
# MAGIC Activities are saved as JSON in the volume for inspection only. **They are not used in any model** until the
# MAGIC numbers have been reviewed (your instruction).

# COMMAND ----------

with open(f"{VOL}/activities.json", "w") as fh:
    json.dump(activities, fh)
print(json.dumps({"plans": plans, "gate": gate, "test_a": test_a, "test_b": test_b}, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Diagnostics: why did activities fail? (no new parsing, no quota)
# MAGIC The first run scored 0% clean, but 14 rows failed *only* on review type even though every detected row contains
# MAGIC "Prior"/"Post". That means the **column mapping** in cell 4 is misaligned, which is a parser bug, not necessarily a
# MAGIC problem with the plans. This cell:
# MAGIC 1. saves the raw `ai_parse_document` output to the volume, so a fixed parser can be re-run **without re-parsing**
# MAGIC    (and parsing is not fully deterministic, see `docs/05_parser_benchmark.md`);
# MAGIC 2. prints, for 3 plans, the joined column names and the first 2 activity rows cell by cell, plus one raw HTML
# MAGIC    table, so the mapping can be fixed from evidence.
# MAGIC
# MAGIC If `rows` is no longer in memory (cluster restarted), re-run cells 1–3 first.

# COMMAND ----------

with open(f"{VOL}/parsed_raw.json", "w") as fh:
    json.dump({r.path.split("/")[-1].replace(".pdf", ""): r.doc for r in rows}, fh)

diag = {}
for r in rows:
    pid = r.path.split("/")[-1].replace(".pdf", "")
    if pid not in ("P147864", "P143751", "P132982"):
        continue
    tabs = [el.get("content", "") for el in walk(json.loads(r.doc)) if el.get("type") == "table"]
    out = []
    for t in tabs:
        g = html_grid(t)
        fa = next((i for i, row in enumerate(g) if is_activity(row)), None)
        if fa is None:
            continue
        out.append({
            "n_header_rows": fa,
            "header_rows": [row[:20] for row in g[:fa]],
            "first_activity_rows": [list(enumerate(row))[:25] for row in g[fa:fa + 2]],
            "row_lengths": sorted({len(row) for row in g}),
            "raw_html": t[:3000],
        })
        if len(out) == 2:
            break
    diag[pid] = {"tables_with_activities": out}
print(json.dumps(diag, indent=1)[:30000])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. One debugging pass: fixed checker + diagnostics + gate re-run (no new parsing)
# MAGIC **Bugs found in run 1:**
# MAGIC 1. *Contradiction:* the clean decision required a Prior/Post review type but the failure explanation skipped that
# MAGIC    rule, so 14 rows showed as "ok" while being counted not clean. Now one function (`check_row`) both decides and
# MAGIC    explains.
# MAGIC 2. *Misaligned columns:* the HTML reader ignored single-quoted `colspan='2'` / `rowspan='2'`, shifting every column
# MAGIC    after a merged header cell. Fixed and unit-tested.
# MAGIC 3. *Fragile mapping:* fields were taken from header positions. Review type, method and status are now found by
# MAGIC    their **values** in each row, and dates are read in several formats (`2023-06-13`, `13-Jun-2023`, `13/06/2023`),
# MAGIC    including a date wrapped across lines inside one cell.
# MAGIC
# MAGIC The checker lives in `src/procplan/check.py` with tests in `tests/test_procplan_check.py` (7 passing locally).
# MAGIC **Stop rule (user):** this is the only debugging pass. If the clean rate is still below 70%, Procurement Plans are
# MAGIC dropped.

# COMMAND ----------

# Self-contained: after a restart, run only section 1 (setup) and then this section. Cell 3 (parsing) is NOT needed
# because the parse was stored by cell 9. That saves ai_parse_document quota and keeps the text identical.
import os, sys, json
from collections import Counter, defaultdict
sys.path.append(os.path.abspath(".."))  # repo root (this notebook lives in notebooks/)
import importlib
import src.procplan.check as ck
importlib.reload(ck)

SLICE = ["P147864", "P163239", "P157054", "P160505", "P155522", "P151449", "P153548", "P132833",
         "P144726", "P143751", "P132775", "P132621", "P132982", "P145778", "P129401", "P121185"]

def walk(obj):
    if isinstance(obj, dict):
        if "type" in obj:
            yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def find_col(names, *need):
    """The OLD header-based lookup, kept only to show what run 1 mapped."""
    for i, n in enumerate(names):
        if all(k in n for k in need):
            return i
    return None

# Use the stored parse from cell 9 if present; otherwise the in-memory result of cell 3.
raw_path = f"{VOL}/parsed_raw.json"
if os.path.exists(raw_path):
    parsed_docs = json.load(open(raw_path))
else:
    parsed_docs = {r.path.split("/")[-1].replace(".pdf", ""): r.doc for r in rows}
    json.dump(parsed_docs, open(raw_path, "w"))
print("plans available from stored parse:", len(parsed_docs))

# COMMAND ----------

def tables_of(doc_json):
    return [el.get("content", "") for el in walk(json.loads(doc_json)) if el.get("type") == "table"]

def is_act(row):
    return any((c or "").strip() in ("Prior", "Post") for c in row)

activities_v2, diag_rows = [], []
for pid, doc in parsed_docs.items():
    header = None
    for t in tables_of(doc):
        g = ck.html_grid(t)
        fa = next((i for i, row in enumerate(g) if is_act(row)), None)
        if fa is None:
            continue
        if fa > 0:
            ncol = max(len(x) for x in g[:fa])
            header = [" ".join((r[i] if i < len(r) else "") for r in g[:fa]).lower() for i in range(ncol)]
        data = [row for row in g[fa:] if is_act(row)]
        names = header or []
        c_sign = ck.date_column(names, data, "signed", "actual") if names else None
        c_note = [i for i in range(len(names)) if "actual" in names[i]
                  and any(k in names[i] for k in ("notice", "invitation", "expression of interest"))]
        for row in data:
            rec = ck.check_row(row)
            sd = ck.first_date(row[c_sign]) if c_sign is not None and c_sign < len(row) else None
            nds = [ck.first_date(row[i]) for i in c_note if i < len(row)]
            nds = [d for d in nds if d]
            rec.update({"pid": pid, "review": rec["review"],
                        "signed_actual": sd.isoformat() if sd else None,
                        "notice_actual": min(nds).isoformat() if nds else None})
            rec["clean"] = rec["reason"] == "clean"
            activities_v2.append(rec)
            if pid in ("P147864", "P143751") and sum(d["pid"] == pid for d in diag_rows) < 5:
                old = {"review_col": find_col(names, "review"), "method_col": find_col(names, "method"),
                       "status_col": find_col(names, "status")}
                old_vals = {k: (row[v] if v is not None and v < len(row) else None) for k, v in old.items()}
                diag_rows.append({"pid": pid, "raw_cells": list(enumerate(row)),
                                  "old_header_mapping": {**old, **{k + "_value": v for k, v in old_vals.items()}},
                                  "new_value_mapping": {k: rec[k] for k in ("ref", "review", "method", "status", "n_dates")},
                                  "bad_date_cells": rec["bad_dates"], "verdict": rec["reason"]})

# COMMAND ----------

# MAGIC %md
# MAGIC ### 10a. Diagnostics: 5 rows from each of 2 plans
# MAGIC For each row: the raw cells (index, text), what the **old** header-based mapping picked, what the **new**
# MAGIC value-based checker found, which cells (if any) look like broken dates, and the verdict.

# COMMAND ----------

for d in diag_rows:
    print(json.dumps(d, ensure_ascii=False)[:2500], "\n")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 10b. Gate re-run with the fixed checker
# MAGIC If it passes (≥ 70%), re-run cells 6 and 7 for tests (a) and (b): they use the `activities` and `gate` variables,
# MAGIC which this cell replaces with the fixed versions. If it fails, Procurement Plans are dropped (stop rule).

# COMMAND ----------

per = defaultdict(lambda: [0, 0])
for a in activities_v2:
    per[a["pid"]][0] += 1
    per[a["pid"]][1] += a["clean"]
n_det, n_clean = len(activities_v2), sum(a["clean"] for a in activities_v2)
gate_v2 = {
    "activities_detected": n_det, "activities_clean": n_clean,
    "parse_rate": round(n_clean / n_det, 3) if n_det else 0.0,
    "passes_70pct_gate": bool(n_det) and n_clean / n_det >= 0.70,
    "plan_coverage": round(sum(1 for p in SLICE if per[p][0] > 0) / len(SLICE), 3),
    "plans_without_activities": [p for p in SLICE if per[p][0] == 0],
    "per_plan_detected_clean": {p: per[p] for p in SLICE},
    "why_not_clean": Counter(a["reason"] for a in activities_v2),
    "clean_with_signed_actual": sum(1 for a in activities_v2 if a["clean"] and a["signed_actual"]),
    "clean_with_signed_and_notice_actual": sum(1 for a in activities_v2 if a["clean"] and a["signed_actual"] and a["notice_actual"]),
}
activities, gate = activities_v2, gate_v2
with open(f"{VOL}/activities_v2.json", "w") as fh:
    json.dump(activities_v2, fh, default=str)
print(json.dumps(gate_v2, indent=2))
