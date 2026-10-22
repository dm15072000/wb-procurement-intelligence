# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 50 — Tender-duration model: P20 / P50 / P80 (Stage 5, M2.1)
# MAGIC
# MAGIC **What this notebook does:** predicts how long a tender takes from its first notice to contract signing, as a
# MAGIC range, and checks honestly whether the model beats a simple baseline. Design: `docs/03_model_rag_design.md` §1.
# MAGIC
# MAGIC **Rules applied (CLAUDE.md, checkpoint d):**
# MAGIC - **main model = prior-review contracts** (625 rows); post-review is a secondary model, reported separately;
# MAGIC - every feature is known on the start-notice date (no later documents; governance from the latest *published*
# MAGIC   year); missing CPIA stays missing with a flag;
# MAGIC - time-based split: the latest 20% of tenders is the test set, never used for fitting;
# MAGIC - results per country, with and without governance features, with and without DS04691;
# MAGIC - the model is promoted to `champion` only if it is at least as good as the current one (§1 promotion rule).
# MAGIC
# MAGIC Runs from silver tables: **no API calls**, no serving endpoint. Logs to MLflow and registers in Unity Catalog.

# COMMAND ----------

import os, sys, json
from collections import defaultdict
sys.path.append(os.path.abspath(".."))
import importlib
import src.models.duration as du
import src.models.serving as sv
for m in (du, sv):
    importlib.reload(m)

import mlflow
import numpy as np
import pandas as pd
import sklearn
from mlflow.models import infer_signature
print("scikit-learn", sklearn.__version__, "| pandas", pd.__version__, "| numpy", np.__version__,
      "(pin the same scikit-learn in requirements.txt for the app)")

CATALOG = "procurement"
S, ML = f"{CATALOG}.silver", f"{CATALOG}.ml"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ML}")
mlflow.set_registry_uri("databricks-uc")
user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{user}/procurement_duration_model")
MODEL_NAME = f"{ML}.tender_duration"
report = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Build one feature row per tender duration
# MAGIC Each kept duration (silver.tender_durations) is joined to its contract, project, country lending type and
# MAGIC governance values. `src/models/duration.py::feature_row` builds the row (unit-tested), so the app builds new
# MAGIC packages with exactly the same code.

# COMMAND ----------

durations = [r.asDict() for r in spark.table(f"{S}.tender_durations").collect()]
contracts = {r.contract_key: r.asDict() for r in spark.table(f"{S}.contracts").where("in_universe").collect()}
projects = {r.project_id: r.asDict() for r in spark.table(f"{S}.projects").where("in_universe").collect()}
lending = {r.iso3: r.lending_type for r in spark.table(f"{S}.countries").collect()}
gov = du.governance_lookup(r.asDict() for r in spark.table(f"{S}.governance").collect())

# Start-notice type of each tender, and the start dates of all tenders per project (for "earlier tenders").
starts = spark.sql(f"""
  SELECT project_id, tender_key, notice_type, notice_date FROM {S}.notices
  WHERE tender_key IS NOT NULL AND notice_date IS NOT NULL
    AND notice_type IN ('Invitation for Bids', 'Request for Expression of Interest')""").collect()
first_notice = {}
for r in starts:
    cur = first_notice.get(r.tender_key)
    if cur is None or r.notice_date < cur[1]:
        first_notice[r.tender_key] = (r.notice_type, r.notice_date, r.project_id)
project_starts = defaultdict(list)
for _, (_, d, pid) in first_notice.items():
    project_starts[pid].append(d)
for v in project_starts.values():
    v.sort()

rows = []
for d in durations:
    c = contracts.get(d["contract_key"])
    if not c:
        continue
    p = projects.get(c["project_id"], {})
    ntype = first_notice.get(d["tender_key"], (None,))[0]
    rows.append(du.feature_row(d, c, p, ntype, project_starts.get(c["project_id"], []), gov,
                               lending.get(c.get("borrower_country_iso3"))))
feat = pd.DataFrame(rows)
feat[du.NUMERIC] = feat[du.NUMERIC].astype(float)
spark.createDataFrame(feat.astype({"start_notice_date": "datetime64[ns]"})) \
     .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ML}.duration_features")
report["rows_by_review_type"] = feat.review_type.value_counts().to_dict()
report["missing_share"] = {c: round(float(feat[c].isna().mean()), 3) for c in du.NUMERIC if feat[c].isna().any()}
print(json.dumps(report, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Train and evaluate (one function, used for the main and the secondary model)
# MAGIC Baseline = median by (category, method) with back-off. GBT = three quantile models (P20, P50, P80) with fixed
# MAGIC hyper-parameters (no tuning, so no validation set is spent). Metrics: MAE and median absolute error of P50,
# MAGIC P80 coverage (should be about 0.80), pinball loss.

# COMMAND ----------

QS = {"p20": 0.2, "p50": 0.5, "p80": 0.8}

def fit_quantiles(train_df, columns):
    cat = [c for c in du.CATEGORICAL if c in columns]
    num = [c for c in du.NUMERIC if c in columns]
    return {q: sv.build_pipeline(cat, num, quantile=v).fit(train_df[cat + num], train_df.duration_days)
            for q, v in QS.items()}, cat + num

def predict(models, columns, df):
    return sv.QuantileDurationModel(models, columns).predict(None, df[columns])

def run(population, review_type, train_filter=None, columns=None, log=True):
    """Train on one review type; return metrics dict. train_filter drops rows from TRAIN only (test is unchanged),
    so ablations are compared on the same test set."""
    data = feat[feat.review_type == review_type].to_dict("records")
    train, test, cutoff = du.time_split(data)
    tr, te = pd.DataFrame(train), pd.DataFrame(test)
    if train_filter is not None:
        tr = tr[train_filter(tr)]
    columns = columns or du.CATEGORICAL + du.NUMERIC
    base50 = du.GroupQuantileBaseline(0.5).fit(tr.to_dict("records"))
    base80 = du.GroupQuantileBaseline(0.8).fit(tr.to_dict("records"))
    models, cols = fit_quantiles(tr, columns)
    pred = predict(models, cols, te)
    y = te.duration_days.tolist()
    out = {"population": population, "n_train": len(tr), "n_test": len(te), "test_from": str(cutoff),
           "baseline": du.evaluate(y, base50.predict(test), p80=base80.predict(test)),
           "gbt": du.evaluate(y, pred.p50.tolist(), p80=pred.p80.tolist(), p20=pred.p20.tolist()),
           "gbt_by_country": du.evaluate_by(test, pred.p50.tolist(), "country_iso3", p80=pred.p80.tolist()),
           "gbt_by_dataset": du.evaluate_by(test, pred.p50.tolist(), "source_dataset", p80=pred.p80.tolist())}
    if log:
        with mlflow.start_run(run_name=population):
            mlflow.log_params({"population": population, "review_type": review_type, "n_train": len(tr),
                               "n_test": len(te), "test_from": str(cutoff), "features": ",".join(cols)})
            for k in ("baseline", "gbt"):
                mlflow.log_metrics({f"{k}_{m}": v for m, v in out[k].items() if isinstance(v, (int, float)) and v is not None})
            mlflow.log_dict(out, "metrics.json")
    return out, models, cols, te

main, main_models, main_cols, main_test = run("prior_review_main", "Prior")
secondary, *_ = run("post_review_secondary", "Post")
print(json.dumps({k: main[k] for k in ("n_train", "n_test", "test_from", "baseline", "gbt")}, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Ablations (same test set): without governance features; without DS04691 training rows
# MAGIC DS04691's prior-review match rate is 31% vs 59% for DS00005 (checkpoint c), so we check whether its rows help.

# COMMAND ----------

no_gov_cols = [c for c in du.CATEGORICAL + du.NUMERIC if c not in du.GOVERNANCE_COLUMNS]
ablation_no_gov, *_ = run("prior_no_governance", "Prior", columns=no_gov_cols)
ablation_no_ds04691, *_ = run("prior_without_DS04691", "Prior", train_filter=lambda d: d.source_dataset != "DS04691")
report["ablations"] = {name: {"n_train": r["n_train"], "mae_p50": r["gbt"]["mae_p50"], "coverage_p80": r["gbt"]["coverage_p80"]}
                       for name, r in (("full", main), ("no_governance", ablation_no_gov),
                                       ("without_DS04691", ablation_no_ds04691))}
print(json.dumps(report["ablations"], indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Feature importance (permutation, on the test set) for the P50 model
# MAGIC Which inputs move the prediction most. Shown in the app next to each prediction.

# COMMAND ----------

from sklearn.inspection import permutation_importance
pi = permutation_importance(main_models["p50"], main_test[main_cols], main_test.duration_days, n_repeats=10,
                            random_state=42, scoring="neg_mean_absolute_error")
importance = sorted(zip(main_cols, pi.importances_mean), key=lambda x: -x[1])
report["permutation_importance_mae_days"] = {c: round(float(v), 1) for c, v in importance}
print(report["permutation_importance_mae_days"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Refit on all prior-review rows, register, and apply the promotion rule
# MAGIC The model registered for the app is refitted on **all** rows (train + test). The promotion decision uses the
# MAGIC held-out metrics above, and compares with the current `champion` on the same test slice.

# COMMAND ----------

from mlflow.tracking import MlflowClient
client = MlflowClient()
all_prior = feat[feat.review_type == "Prior"]
final_models, final_cols = fit_quantiles(all_prior, du.CATEGORICAL + du.NUMERIC)
wrapper = sv.QuantileDurationModel(final_models, final_cols)
example = all_prior[final_cols].head(5)

champion_metrics = None
try:
    champ = client.get_model_version_by_alias(MODEL_NAME, "champion")
    champ_model = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@champion")
    cp = champ_model.predict(main_test[final_cols])
    champion_metrics = du.evaluate(main_test.duration_days.tolist(), cp.p50.tolist(), p80=cp.p80.tolist())
except Exception as e:
    print("no current champion:", type(e).__name__)

with mlflow.start_run(run_name="prior_review_register"):
    mlflow.log_metrics({f"heldout_{k}": v for k, v in main["gbt"].items() if isinstance(v, (int, float)) and v is not None})
    info = mlflow.pyfunc.log_model(
        "model", python_model=wrapper, code_paths=[os.path.abspath("../src")],
        signature=infer_signature(example, wrapper.predict(None, example)), input_example=example,
        registered_model_name=MODEL_NAME,
        # exact versions, so the app un-pickles the trees with the same scikit-learn that trained them
        pip_requirements=[f"scikit-learn=={sklearn.__version__}", f"pandas=={pd.__version__}", f"numpy=={np.__version__}"])
version = info.registered_model_version
promote, why = du.promote(main["gbt"], champion_metrics)
client.set_model_version_tag(MODEL_NAME, version, "heldout_mae_p50", f"{main['gbt']['mae_p50']:.1f}")
client.set_model_version_tag(MODEL_NAME, version, "heldout_coverage_p80", f"{main['gbt']['coverage_p80']:.3f}")
client.set_model_version_tag(MODEL_NAME, version, "n_train", str(len(all_prior)))
client.set_model_version_tag(MODEL_NAME, version, "promotion_decision", f"{promote}: {why}")
if promote:
    client.set_registered_model_alias(MODEL_NAME, "champion", version)
report["registered"] = {"model": MODEL_NAME, "version": version, "promoted": promote, "reason": why,
                        "champion_metrics_on_same_test": champion_metrics}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Summary: copy back for docs/07_evaluation.md

# COMMAND ----------

summary = {"main_prior_review": {k: main[k] for k in ("n_train", "n_test", "test_from", "baseline", "gbt", "gbt_by_country",
                                                       "gbt_by_dataset")},
           "secondary_post_review": {k: secondary[k] for k in ("n_train", "n_test", "test_from", "baseline", "gbt")},
           **report}
spark.createDataFrame([(json.dumps(summary, default=str),)], "summary string") \
     .write.mode("overwrite").saveAsTable(f"{ML}.duration_model_report")
print(json.dumps(summary, indent=2, default=str))
