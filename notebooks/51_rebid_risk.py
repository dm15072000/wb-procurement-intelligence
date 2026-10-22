# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 51 — Re-bid or cancellation risk (Stage 5, M2.1)
# MAGIC
# MAGIC **What this notebook does:** labels every universe tender from the public record with two outcomes, **re-bid**
# MAGIC (advertised again) and **cancellation** (notice or plan entry cancelled), reported separately, then estimates "how
# MAGIC often were similar tenders re-bid or cancelled?" on the combined outcome, always named **"re-bid or
# MAGIC cancellation"**. A cancellation is not always a re-bid (P144726: 5 of 10 cancelled entries were re-advertised under
# MAGIC a new reference, 5 dropped). Design: `docs/03_model_rag_design.md` §2a.
# MAGIC
# MAGIC **Why rates first, not a classifier:** H2 found re-bid signals are rare (9 of 3,643 IFB references had a second
# MAGIC IFB). With few positives a classifier learns noise. So the default is a smoothed rate per group with its count
# MAGIC and a 90% interval. A classifier is trained **only if there are at least 50 positives**, and kept only if it
# MAGIC beats the rates on the time-based test set (Brier score) **and** has AUC ≥ 0.6. The gate is decided by the data
# MAGIC and logged.
# MAGIC
# MAGIC **Checkpoint (d) change 6:** IFB notices that only modify an earlier one (corrigendum, addendum, amendment,
# MAGIC deadline extension, status "Revised") do not count as a second invitation. A sample of excluded notices is
# MAGIC printed in section 5 for review.
# MAGIC
# MAGIC No API calls. Inputs: silver.notices, silver.contracts, silver.projects, silver.plan_activity_labels.

# COMMAND ----------

import os, sys, json
from collections import Counter, defaultdict
sys.path.append(os.path.abspath(".."))
import importlib
import src.risk.labels as lb
import src.risk.readvertise as rv
import src.models.duration as du
for m in (lb, rv, du):
    importlib.reload(m)
import pandas as pd

CATALOG = "procurement"
S, G, ML = (f"{CATALOG}.{x}" for x in ("silver", "gold", "ml"))
MIN_POSITIVES_FOR_CLASSIFIER = 50
# Keep a classifier only if it beats the rates on Brier AND ranks tenders meaningfully better than chance. Brier alone
# is not enough: when the test period has far fewer positives than training (1.7% vs 16.6% on 2026-10-22, because
# P157054's re-advertisements were in 2017-2022), a model that predicts "low" for everything wins on Brier with AUC 0.46.
MIN_AUC_TO_KEEP = 0.6
report = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Label every tender that had an Invitation for Bids
# MAGIC Reasons are kept per tender (`ifb_notices:2`, `notice_cancelled`, `text:re-tender`, `plan:cancelled`), so any
# MAGIC label can be checked by eye.

# COMMAND ----------

notices = defaultdict(list)
for r in spark.sql(f"""SELECT n.* FROM {S}.notices n JOIN {S}.projects p ON n.project_id = p.project_id
                       WHERE p.in_universe AND n.tender_key IS NOT NULL""").collect():
    notices[r.tender_key].append(r.asDict())
plan = defaultdict(list)
for r in spark.table(f"{S}.plan_activity_labels").collect():
    if r.project_id and r.ref_key:
        plan[f"{r.project_id}:{r.ref_key}"].append(r.asDict())
country_of = {r.project_id: (r.country_iso3_list[0] if r.country_iso3_list else None)
              for r in spark.table(f"{S}.projects").where("in_universe").collect()}

# Contract category and method for tenders that reached a contract (same key as the duration join).
contract_of = {f"{r.project_id}:{r.ref_key}": r.asDict() for r in spark.sql(
    f"SELECT project_id, ref_key, procurement_category, procurement_method FROM {S}.contracts "
    f"WHERE in_universe AND ref_key IS NOT NULL").collect()}

tenders = []
for tk, ns in notices.items():
    ifbs = [n for n in ns if n["notice_type"] == "Invitation for Bids" and n["notice_date"]]
    if not ifbs:
        continue
    lab = lb.tender_label(ns, plan.get(tk, ()))
    first = min(ifbs, key=lambda n: n["notice_date"])
    c = contract_of.get(tk)
    tenders.append({"tender_key": tk, "project_id": first["project_id"], "country_iso3": country_of.get(first["project_id"]),
                    "start_date": first["notice_date"], "notice_group": first["procurement_group"],
                    "notice_method_code": first["procurement_method_code"],
                    "contract_category": c["procurement_category"] if c else None,
                    "contract_method": c["procurement_method"] if c else None,
                    "description": first.get("bid_description"), "has_contract": c is not None,
                    "rebid": lab["rebid"], "cancellation": lab["cancellation"],
                    "rebid_or_cancellation": lab["rebid_or_cancellation"], "reasons": lab["reasons"]})
report["tenders"] = len(tenders)

# Same package re-advertised under a NEW reference (user decision 2026-10-22, src/risk/readvertise.py): the earlier
# attempt (no contract) is flagged re_advertised_new_ref, reported separately and counted in "re-bid or cancellation".
# Measured precision before this run: 29 of 30 random flags correct (the wrong one led to the "numbers in order" fix)
# and 44 of 44 outside P157054; 10 of 10 flags added by the spacing fix ("MP04WB-03" = "MP04 WB-03") correct.
readv_flags, readv_chains, report["readvertisement"] = rv.find_chains(
    [{"tender_key": t["tender_key"], "project_id": t["project_id"], "start_date": t["start_date"],
      "description": t["description"], "has_contract": t["has_contract"]} for t in tenders])
for t in tenders:
    f = readv_flags.get(t["tender_key"])
    t["re_advertised_new_ref"] = bool(f)
    if f:
        t["reasons"].append(f"readvertised:{f['later_tender_key'].split(':', 1)[1]}")
        t["rebid_or_cancellation"] = True
spark.createDataFrame([(f"{g[0]['tender_key']}", i, t["tender_key"], t["start_date"], t["has_contract"], t["description"])
                       for g in readv_chains for i, t in enumerate(g)],
                      "chain_first_tender_key string, round int, tender_key string, start_date date, has_contract boolean, "
                      "description string")      .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.readvertisement_chains")
# Checkpoint d change 6: corrigendum / amendment / extension / revised IFB notices modify an invitation, they are not a
# second round of bidding, so they never count toward "more than one IFB". How many were set aside:
all_ifbs = [n for ns in notices.values() for n in ns if n["notice_type"] == "Invitation for Bids"]
report["ifb_notices_excluded_as_modifications"] = sum(lb.is_modification(n) for n in all_ifbs)
report["ifb_notices"] = len(all_ifbs)
# Two outcomes reported separately; the rates and the classifier use the combined one, "re-bid or cancellation".
OUTCOME = "rebid_or_cancellation"
report["positives"] = sum(t[OUTCOME] for t in tenders)
report["positives_by_outcome"] = {"rebid": sum(t["rebid"] for t in tenders),
                                  "re_advertised_new_ref": sum(t["re_advertised_new_ref"] for t in tenders),
                                  "re_advertised_and_rebid_wording": sum(t["re_advertised_new_ref"] and t["rebid"]
                                                                         for t in tenders),
                                  "cancellation": sum(t["cancellation"] for t in tenders),
                                  "both": sum(t["rebid"] and t["cancellation"] for t in tenders),
                                  "rebid_or_cancellation": report["positives"]}
report["reasons"] = dict(Counter(r.split(":")[0] for t in tenders for r in t["reasons"]))
print(json.dumps(report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Map notice groups to contract categories (data-driven, printed for review)
# MAGIC Notices use their own group and method codes; contracts use category and method names. The app works with
# MAGIC contract names, so each notice group / method code is mapped to the contract value it co-occurs with most often
# MAGIC among tenders that reached a contract. Tenders without a contract use this mapping.

# COMMAND ----------

def majority_map(pairs):
    counts = defaultdict(Counter)
    for k, v in pairs:
        if k and v:
            counts[k][v] += 1
    return {k: c.most_common(1)[0][0] for k, c in counts.items()}, {k: dict(c.most_common(3)) for k, c in counts.items()}

group_map, group_evidence = majority_map((t["notice_group"], t["contract_category"]) for t in tenders)
method_map, method_evidence = majority_map((t["notice_method_code"], t["contract_method"]) for t in tenders)
print("notice group -> contract category:", json.dumps(group_evidence, indent=1))
print("notice method code -> contract method:", json.dumps(method_evidence, indent=1))
for t in tenders:
    t["procurement_category"] = t["contract_category"] or group_map.get(t["notice_group"]) or t["notice_group"] or "unknown"
    t["procurement_method"] = t["contract_method"] or method_map.get(t["notice_method_code"]) or "unknown"

spark.createDataFrame(pd.DataFrame([dict(t, reasons="; ".join(t["reasons"])) for t in tenders])
                      .astype({"start_date": "datetime64[ns]"})) \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.rebid_labels")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Smoothed rates with a time-based check
# MAGIC Fit the rates on the earlier 80% of tenders and score the latest 20% (Brier score, vs. the global rate). Then
# MAGIC refit on all tenders and save `gold.rebid_rates` for the app.

# COMMAND ----------

train, test, cutoff = du.time_split(tenders, "start_date")
rates = lb.HierarchicalRates().fit(train)
glob = sum(t[OUTCOME] for t in train) / max(1, len(train))
brier = lambda ps: sum((p - t[OUTCOME]) ** 2 for p, t in zip(ps, test)) / max(1, len(test))
report["rates_check"] = {"test_from": str(cutoff), "n_train": len(train), "n_test": len(test),
                         "outcome": "re-bid or cancellation", "test_positives": sum(t[OUTCOME] for t in test),
                         "brier_hierarchical": brier([rates.predict_one(t)["rate"] for t in test]),
                         "brier_global_rate": brier([glob] * len(test))}

final = lb.HierarchicalRates().fit(tenders)
rows = []
for (lvl, g), v in final.table.items():
    vals = dict(zip(lvl, g))
    rows.append({"level": v["level"], "depth": len(lvl), "country_iso3": vals.get("country_iso3"),
                 "procurement_category": vals.get("procurement_category"), "procurement_method": vals.get("procurement_method"),
                 "rate": v["rate"], "positives": v["positives"], "n": v["n"], "ci_low": v["ci90"][0], "ci_high": v["ci90"][1]})
spark.createDataFrame(rows, "level string, depth int, country_iso3 string, procurement_category string, "
                      "procurement_method string, rate double, positives int, n int, ci_low double, ci_high double") \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.rebid_rates")
print(json.dumps(report["rates_check"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3b. Durations from the FIRST notice of a re-advertisement chain (kept next to the original)
# MAGIC For packages advertised several times under new references that end in a signed contract, the time from the
# MAGIC first notice of the chain to signing. The original duration (from the notice of the round that got the contract)
# MAGIC is NOT changed: both are saved in `gold.tender_durations_chain`. The duration model keeps the original as its
# MAGIC target (decision 2026-10-22, docs/07 §1): the chain time is reported as context, and the risk of having to
# MAGIC re-advertise is carried by the "re-bid or cancellation" rate.

# COMMAND ----------

from collections import defaultdict as _dd
dur_by_tender = _dd(list)
for r in spark.table(f"{S}.tender_durations").collect():
    dur_by_tender[r.tender_key].append(r.asDict())
chain_rows = rv.chain_durations(readv_chains, dur_by_tender)
if chain_rows:
    spark.createDataFrame(pd.DataFrame(chain_rows).astype({"first_notice_date": "datetime64[ns]",
                                                           "start_notice_date": "datetime64[ns]",
                                                           "signing_date": "datetime64[ns]"}))          .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{G}.tender_durations_chain")
added = sorted(r["added_days"] for r in chain_rows)
q = lambda p: added[min(len(added) - 1, int(p * len(added)))] if added else None
report["chain_durations"] = {
    "durations_total": sum(len(v) for v in dur_by_tender.values()), "durations_with_earlier_rounds": len(chain_rows),
    "by_review_type": dict(Counter(r["review_type"] for r in chain_rows)),
    "added_days_p25_p50_p75_max": [q(0.25), q(0.5), q(0.75), added[-1] if added else None],
    "over_3_years_from_first_notice": sum(r["duration_days_from_first_notice"] > 1095 for r in chain_rows)}
print(json.dumps(report["chain_durations"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Classifier, only if there are enough positives (gate decided here and logged)

# COMMAND ----------

if report["positives"] >= MIN_POSITIVES_FOR_CLASSIFIER and report["rates_check"]["test_positives"] > 0:
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OrdinalEncoder
    cats = ["procurement_category", "procurement_method", "country_iso3"]
    tr, te = pd.DataFrame(train), pd.DataFrame(test)
    clf = Pipeline([("pre", ColumnTransformer([("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                                                                    encoded_missing_value=-1), cats)])),
                    ("gbt", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                                           categorical_features=[0, 1, 2], random_state=42))])
    clf.fit(tr[cats], tr[OUTCOME])
    p = clf.predict_proba(te[cats])[:, 1]
    auc, brier_clf = float(roc_auc_score(te[OUTCOME], p)), float(brier_score_loss(te[OUTCOME], p))
    report["classifier"] = {"auc": auc, "brier": brier_clf, "min_auc_to_keep": MIN_AUC_TO_KEEP,
                            "kept": brier_clf < report["rates_check"]["brier_hierarchical"] and auc >= MIN_AUC_TO_KEEP}
else:
    report["classifier"] = {"trained": False, "reason": f"{report['positives']} positives < {MIN_POSITIVES_FOR_CLASSIFIER} "
                                                        "or none in the test period: rates only"}
print(json.dumps(report["classifier"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Review sample and summary
# MAGIC 20 random positive labels with their reasons: **please mark each correct / wrong** to measure label precision.

# COMMAND ----------

import random
random.seed(7)
pos = [t for t in tenders if t[OUTCOME]]
for t in random.sample(pos, min(20, len(pos))):
    ns = notices[t["tender_key"]]
    print(t["tender_key"], t["start_date"], t["reasons"], "|", (ns[0].get("bid_description") or "")[:100])
print("\n=== 10 IFB notices excluded as modifications (please check they are corrigenda / extensions) ===")
for n in random.sample([n for n in all_ifbs if lb.is_modification(n)],
                       min(10, report["ifb_notices_excluded_as_modifications"])):
    print(n["tender_key"], n["notice_date"], n["notice_status"], "|", (n.get("bid_description") or "")[:110])
spark.createDataFrame([(json.dumps(report, default=str),)], "summary string") \
     .write.mode("overwrite").saveAsTable(f"{ML}.rebid_report")
print(json.dumps(report, indent=2, default=str))
