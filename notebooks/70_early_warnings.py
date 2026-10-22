# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 70 — Execution & monitoring: early warnings, amendment indicator, backtest (Stage 5, L3, M2.1)
# MAGIC
# MAGIC **What this notebook does:** for running projects, raises explained flags (`src/monitoring/early_warning.py`):
# MAGIC tenders open longer than the model's P80, disbursement far behind elapsed time, weak latest ISR ratings, and
# MAGIC recently reported procurement problems. It also computes the transparent **amendment indicator score**
# MAGIC (`src/risk/amendment.py`; a heuristic, not a probability) for slice contracts, and backtests the flags on closed
# MAGIC slice projects. Design: `docs/03_model_rag_design.md` §2b and §6.
# MAGIC
# MAGIC No API calls. Uses the duration model registered by notebook 50 (if present) and the outputs of notebook 55.

# COMMAND ----------

import os, sys, json
from collections import defaultdict
from datetime import date, timedelta
sys.path.append(os.path.abspath(".."))
import importlib
import src.monitoring.early_warning as ew
import src.models.duration as du
import src.risk.amendment as am
import src.conflict.ratings as ra
for m in (ew, du, am, ra):
    importlib.reload(m)
import mlflow
import pandas as pd

CATALOG = "procurement"
S, G = f"{CATALOG}.silver", f"{CATALOG}.gold"
TODAY = date.today()
report = {}
mlflow.set_registry_uri("databricks-uc")
try:
    model = mlflow.pyfunc.load_model(f"models:/{CATALOG}.ml.tender_duration@champion")
except Exception as e:
    model = None
    print("no duration model (run notebook 50 first); the tender-overdue flag is skipped:", type(e).__name__)

projects = {r.project_id: r.asDict() for r in spark.table(f"{S}.projects").where("in_universe").collect()}
lending = {r.iso3: r.lending_type for r in spark.table(f"{S}.countries").collect()}
gov = du.governance_lookup(r.asDict() for r in spark.table(f"{S}.governance").collect())
ratings = defaultdict(list)
for r in spark.table(f"{G}.ratings").collect():
    ratings[r.project_id].append(r.asDict())
events = defaultdict(list)
if spark.catalog.tableExists(f"{S}.contract_events"):
    for r in spark.table(f"{S}.contract_events").collect():
        events[r.project_id].append(r.asDict())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Open tenders and their predicted P80
# MAGIC An open tender = an Invitation for Bids / REOI with no matched contract and no cancelled notice, in an active
# MAGIC universe project. Caveat (stated in the app): the model is trained on prior-review contracts, and an unmatched
# MAGIC notice can also be a join failure, so this flag means "check this tender", not "this tender is late".

# COMMAND ----------

open_tenders = []
if model is not None:
    rows = spark.sql(f"""
      SELECT l.tender_key, l.project_id, l.start_date, l.procurement_category, l.procurement_method
      FROM {G}.rebid_labels l JOIN {S}.projects p ON l.project_id = p.project_id
      WHERE p.in_universe AND p.status = 'Active' AND NOT array_contains(split(l.reasons, '; '), 'notice_cancelled')
        AND l.tender_key NOT IN (SELECT concat(project_id, ':', ref_key) FROM {S}.contracts WHERE ref_key IS NOT NULL)
    """).collect()
    starts = defaultdict(list)
    for r in spark.sql(f"SELECT project_id, start_date FROM {G}.rebid_labels").collect():
        starts[r.project_id].append(r.start_date)
    for v in starts.values():
        v.sort()
    feats = []
    for r in rows:
        p = projects.get(r.project_id, {})
        iso3 = (p.get("country_iso3_list") or [None])[0]
        feats.append(du.feature_row({"contract_key": r.tender_key, "start_notice_date": r.start_date, "duration_days": None},
                                    {"project_id": r.project_id, "procurement_category": r.procurement_category,
                                     "procurement_method": r.procurement_method, "borrower_country_iso3": iso3,
                                     "total_amount_usd": None},
                                    p, "Invitation for Bids", starts[r.project_id], gov, lending.get(iso3)))
    if feats:
        X = pd.DataFrame(feats)
        X[du.NUMERIC] = X[du.NUMERIC].astype(float)
        pred = model.predict(X[du.CATEGORICAL + du.NUMERIC])
        open_tenders = [{"tender_key": f["contract_key"], "project_id": f["project_id"],
                         "start_notice_date": f["start_notice_date"], "p80_days": float(p80)}
                        for f, p80 in zip(feats, pred.p80)]
report["open_tenders"] = len(open_tenders)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Flags per active project

# COMMAND ----------

disb = defaultdict(list)
for r in spark.table(f"{S}.disbursements").collect():
    if r.project_id:
        disb[r.project_id].append(r.asDict())
open_by_project = defaultdict(list)
for t in open_tenders:
    open_by_project[t["project_id"]].append(t)

flags = []
for pid, p in projects.items():
    if p.get("status") != "Active":
        continue
    fl = (ew.overdue_tenders(open_by_project.get(pid, []), TODAY)
          + ew.disbursement_lag(disb.get(pid, []), p.get("board_approval_date"), p.get("closing_date"), TODAY,
                                p.get("total_commitment_usd"))
          + ew.rating_flags(ratings.get(pid, []), TODAY) + ew.event_flags(events.get(pid, []), TODAY))
    flags += [dict(f, project_id=pid, as_of=TODAY) for f in fl]
spark.createDataFrame(flags or [], "flag string, key string, reason string, evidence string, project_id string, as_of date") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.early_warnings")
report["flags"] = pd.Series([f["flag"] for f in flags]).value_counts().to_dict() if flags else {}
report["active_projects_with_flags"] = len({f["project_id"] for f in flags})
print(json.dumps(report, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Amendment indicator score for slice contracts (heuristic, not a probability)
# MAGIC Each point shows its factor; unknown inputs are listed as unknown, never counted as safe.

# COMMAND ----------

contracts = [r.asDict() for r in spark.sql(f"""
  SELECT c.* FROM {S}.contracts c JOIN {S}.projects p ON c.project_id = p.project_id WHERE p.in_slice""").collect()]
p75 = {r.procurement_category: r.p75 for r in spark.sql(f"""
  SELECT procurement_category, percentile(total_amount_usd, 0.75) AS p75 FROM {S}.contracts
  WHERE in_universe AND total_amount_usd IS NOT NULL GROUP BY procurement_category""").collect()}
scores = []
for c in contracts:
    obs = [o for o in ratings.get(c["project_id"], []) if o["doc_type"] == ra.ISR_TYPE
           and o["dimension"] in ("implementation", "procurement")]
    latest = {}
    for o in sorted(obs, key=lambda o: o["doc_date"]):
        latest[o["dimension"]] = o["score"]
    earlier_events = [e for e in events.get(c["project_id"], [])
                      if e["event_type"] in ("amendment", "time_extension", "cost_overrun")
                      and c["signing_date"] and e["doc_date"] < c["signing_date"]]
    iso3 = c.get("borrower_country_iso3")
    g = du.governance_asof(gov, iso3, c["signing_date"]) if c["signing_date"] else {}
    s = am.score({"category": c["procurement_category"], "amount_usd": c["total_amount_usd"],
                  "category_p75_usd": p75.get(c["procurement_category"]), "p80_days": None,
                  "wgi_ge": g.get("wgi_ge"), "latest_isr_min_score": min(latest.values()) if latest else None,
                  "n_earlier_amendment_events": len(earlier_events) if events else None})
    scores.append({"contract_key": c["contract_key"], "project_id": c["project_id"], "score": s["score"],
                   "max_known": s["max_known"], "factors": json.dumps(s["factors"]), "unknown": s["unknown"]})
spark.createDataFrame(scores, "contract_key string, project_id string, score int, max_known int, factors string, "
                      "unknown array<string>").write.mode("overwrite").option("overwriteSchema", "true") \
     .saveAsTable(f"{G}.amendment_scores")

# Descriptive check (n = 16 projects, not a validation): mean score vs extracted amendment-type events per project.
per_proj = pd.DataFrame(scores).groupby("project_id").score.mean().rename("mean_score").to_frame()
per_proj["amendment_events"] = [sum(e["event_type"] in ("amendment", "time_extension", "cost_overrun")
                                    for e in events.get(pid, [])) for pid in per_proj.index]
report["amendment_score_vs_events_spearman_descriptive"] = float(per_proj.corr(method="spearman").iloc[0, 1]) \
    if len(per_proj) > 2 and per_proj.amendment_events.nunique() > 1 else None
print(per_proj.sort_values("mean_score", ascending=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Backtest on closed slice projects (descriptive, n ≤ 16)
# MAGIC For each project with an ICR outcome rating: which flags would have been raised one year before the ICR, and was
# MAGIC the ICR outcome Moderately Unsatisfactory or worse? This shows whether flags *precede* bad outcomes; it is too
# MAGIC small to be a validation and is reported as such.

# COMMAND ----------

bt = []
for pid, obs in ratings.items():
    icr = [o for o in obs if o["doc_type"] == ra.ICR_TYPE and o["dimension"] == "outcome"]
    if not icr or pid not in projects:
        continue
    icr = icr[0]
    asof = icr["doc_date"] - timedelta(days=365)
    p = projects[pid]
    fl = (ew.disbursement_lag(disb.get(pid, []), p.get("board_approval_date"), p.get("closing_date"), asof,
                              p.get("total_commitment_usd"))
          + ew.rating_flags(obs, asof) + ew.event_flags(events.get(pid, []), asof))
    bt.append({"project_id": pid, "icr_outcome": icr["value"], "bad_outcome": icr["score"] <= 3,
               "flags_one_year_before": sorted({f["flag"] for f in fl})})
bt_df = pd.DataFrame(bt)
print(bt_df.to_string())
if len(bt_df):
    bt_df["any_flag"] = bt_df.flags_one_year_before.map(bool)
    report["backtest"] = {"projects": len(bt_df), "bad_outcomes": int(bt_df.bad_outcome.sum()),
                          "crosstab_any_flag_vs_bad_outcome": pd.crosstab(bt_df.any_flag, bt_df.bad_outcome).to_dict()}
print(json.dumps(report, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC Return the summary, so a job run's result can be read without opening the notebook.

# COMMAND ----------

dbutils.notebook.exit(json.dumps(report, default=str))
