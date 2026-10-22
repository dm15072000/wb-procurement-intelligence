# 07 — Evaluation results (Stage 6)

Status: **template: no results yet.** Every cell marked _[run]_ is filled from the summary cell of the named
notebook after you run it. Nothing here may be filled by estimate (CLAUDE.md: never invent results).

## 1. Tender duration (notebook 50, `ml.duration_model_report`)

| | n train | n test | test from | P50 MAE (days) | median AE | P80 coverage | pinball 0.8 |
|---|---|---|---|---|---|---|---|
| Baseline (median by category × method), prior review | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| GBT quantile, **prior review (main)** | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| GBT, post review (secondary) | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| Ablation: no governance features | _[run]_ | | | _[run]_ | | _[run]_ | |
| Ablation: without DS04691 training rows | _[run]_ | | | _[run]_ | | _[run]_ | |

Per country (prior review, test set): _[run]_ · Per dataset: _[run]_ · Top permutation importances: _[run]_ ·
Promotion decision: _[run]_

## 2. Re-bid risk (notebook 51, `ml.rebid_report`)

Tenders _[run]_ · positives _[run]_ · reasons _[run]_ · Brier hierarchical vs global _[run]_ · classifier trained?
_[run]_ · label precision on 20 reviewed positives: _[your review]_

## 3. Extraction (notebook 55)

Ratings rows _[run]_ · conflicts by flag _[run]_ · lessons kept / rejected _[run]_ · events kept / rejected _[run]_ ·
precision on 20 lessons and 20 events: _[your review]_ · ratings sample check (20): _[your review]_

## 4. Retrieval and answers (notebook 61, `ml.rag_eval_results`)

All metrics are on the **test half**. The refusal threshold was set on the calibration half (checkpoint d change 4).

| Test half | n | recall@5 | recall@10 | MRR | citation validity | claim support | refusal accuracy |
|---|---|---|---|---|---|---|---|
| All | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| Reviewed questions only | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ | _[run]_ |
| By kind: rating / lesson / tender design (L2) / contract / refusal | _[run]_ | | | | | | |
| India / China / Mali / Albania / Sri Lanka | _[run]_ | | | | | | |

Retrieval path (hybrid or keyword fallback) _[run]_ · gate threshold and calibration-half pass rate _[run]_ ·
claims removed: mechanical _[run]_ / verifier unsupported _[run]_ / no verdict _[run]_ · judge second opinion _[run]_ ·
rating-answer accuracy _[run]_ · synthetic-question word overlap (median; count above 0.5) _[run]_ · prompt injection
(quarantined? planted claim survived?) _[run]_ · notice chunks indexed (notebook 21) _[run]_ · hybrid identifier check
(notebook 60) _[run]_ · Re-bid: IFB notices excluded as modifications (notebook 51) _[run]_

## 5. Early warnings and backtest (notebook 70)

Flags by type _[run]_ · backtest crosstab (any flag one year before ICR vs outcome ≤ MU), n ≤ 16, descriptive _[run]_ ·
amendment indicator vs extracted events (Spearman, descriptive) _[run]_
