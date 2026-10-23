# KPI dashboard (Databricks AI/BI, Lakeview)

One dashboard, defined as code, deployed by `notebooks/85_kpi_dashboard.py` and refreshed by the weekly job (last task,
`run_if: ALL_DONE`). The app links to it from its sidebar (link read from `ops.config` key `kpi_dashboard_url`).

| File | What it is |
|---|---|
| `kpi_views.sql` | 10 read-only views in `procurement.ops` (prefix `kpi_`) over existing tables. No values are typed in, except the proposed pilot targets copied from docs/08 §6.2–6.3. |
| `build_dashboard.py` | Writes `procurement_kpis.lvdash.json` (datasets + 4 pages of widgets). Run after any change: `python dashboards/build_dashboard.py`. |
| `procurement_kpis.lvdash.json` | The dashboard definition that notebook 85 deploys. `tests/test_dashboard.py` checks it matches the builder, every widget reads a declared dataset column, every dataset reads a defined view, and widgets don't overlap. |

## Pages and sources

| Page | Widgets | Source views → tables |
|---|---|---|
| 1. Data health | last refresh + row count per source; data-quality gate latest pass/fail and history; contract coverage by FY (with status label); missing-value rates per dataset | `kpi_source_refresh` (bronze `ingested_at`, silver, docs), `kpi_dq_runs` (`ops.data_quality_runs`), `kpi_coverage_fy` (`gold.contract_coverage_fy`), `kpi_missing_rates` (`silver.contracts`, road universe) |
| 2. Model quality | duration MAE (model vs median baseline) and P80 coverage, prior review (champion) and post review; re-bid / re-advertisement / cancellation rates, all tenders vs excluding P157054 | `kpi_duration_model` (`ml.duration_model_report`), `kpi_rebid_rates` (`gold.rebid_labels`) |
| 3. Assistant quality | latest run: recall@5 strict and lenient, citation validity, claim support, claims removed by guardrails, refusal accuracy; recall trend across runs; all runs | `kpi_eval_runs` (`ml.rag_eval_results`, test half only) |
| 4. Usage & impact | requests per day, latency P50/P90, LLM calls, 👍/👎, wrong-source comments, **labelled TEST USAGE ONLY**; business KPIs as **PROPOSED pilot targets** | `kpi_usage_daily` (`ops.rag_requests`), `kpi_feedback_daily` (`ops.feedback`), `kpi_proposed_targets` (docs/08) |

Rules: coverage before FY2020 is partial, so a low count is not "no activity"; usage numbers are developer tests until a
pilot starts; business KPIs have no measured values yet, and none are invented. LLM calls per request = answer +
claim verifier (from the timings the app logs).
