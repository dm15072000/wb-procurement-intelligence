-- KPI dashboard views (dashboards/README.md). Small read-only views over EXISTING tables; nothing is copied or invented.
-- Created by notebooks/85_kpi_dashboard.py. Statements are separated by a line containing only ";".

-- 1a. Last refresh and row count per source table
CREATE OR REPLACE VIEW procurement.ops.kpi_source_refresh AS
SELECT 'DS00005 contract awards (FY2020+)' AS source, 'bronze' AS layer, max(ingested_at) AS last_refresh, count(*) AS row_count FROM procurement.bronze.ds00005_contract_awards
UNION ALL SELECT 'DS04691 contract awards (FY2017-19)', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.ds04691_contract_awards
UNION ALL SELECT 'DS01004 contract awards (FY2001-16, prior review)', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.ds01004_contract_awards
UNION ALL SELECT 'Procurement notices', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.procurement_notices
UNION ALL SELECT 'Projects', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.projects
UNION ALL SELECT 'Disbursements', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.disbursements
UNION ALL SELECT 'Governance (WGI, CPIA)', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.governance
UNION ALL SELECT 'Countries', 'bronze', max(ingested_at), count(*) FROM procurement.bronze.countries
UNION ALL SELECT 'Contracts (de-duplicated)', 'silver', CAST(NULL AS TIMESTAMP), count(*) FROM procurement.silver.contracts
UNION ALL SELECT 'Document + notice passages (embedded)', 'docs', max(embedded_at), count(*) FROM procurement.docs.chunk_embeddings
;
-- 1b. Data-quality gate (notebook 12): one row per run
CREATE OR REPLACE VIEW procurement.ops.kpi_dq_runs AS
SELECT run_ts, count(*) AS checks, sum(CASE WHEN passed THEN 1 ELSE 0 END) AS passed,
       sum(CASE WHEN passed THEN 0 ELSE 1 END) AS failed,
       CASE WHEN min(CAST(passed AS INT)) = 1 THEN 'PASS' ELSE 'FAIL' END AS gate
FROM procurement.ops.data_quality_runs GROUP BY run_ts
;
-- 1c. Contract coverage by fiscal year (share of published award notices matched in the contract data)
CREATE OR REPLACE VIEW procurement.ops.kpi_coverage_fy AS
SELECT fiscal_year, coverage_status, n_contracts, award_notices, matched, round(coverage_pct, 3) AS coverage_share
FROM procurement.gold.contract_coverage_fy
;
-- 1d. Missing-value rates of key contract fields, road-sector universe, per dataset
CREATE OR REPLACE VIEW procurement.ops.kpi_missing_rates AS
SELECT source_dataset, count(*) AS contracts,
       round(avg(CASE WHEN signing_date IS NULL THEN 1 ELSE 0 END), 4) AS signing_date_missing,
       round(avg(CASE WHEN total_amount_usd IS NULL THEN 1 ELSE 0 END), 4) AS amount_missing,
       round(avg(CASE WHEN procurement_category IS NULL THEN 1 ELSE 0 END), 4) AS category_missing,
       round(avg(CASE WHEN procurement_method IS NULL THEN 1 ELSE 0 END), 4) AS method_missing,
       round(avg(CASE WHEN review_type IS NULL OR review_type = 'unknown' THEN 1 ELSE 0 END), 4) AS review_type_unknown,
       round(avg(CASE WHEN borrower_country_iso3 IS NULL THEN 1 ELSE 0 END), 4) AS country_missing
FROM procurement.silver.contracts WHERE in_universe GROUP BY source_dataset
;
-- 2a. Duration model (notebook 50): held-out MAE vs median baseline and P80 coverage, per population
CREATE OR REPLACE VIEW procurement.ops.kpi_duration_model AS
SELECT 'Prior review (main)' AS population,
       try_cast(summary:main_prior_review.n_test AS INT) AS n_test,
       summary:main_prior_review.test_from::string AS test_from,
       round(try_cast(summary:main_prior_review.baseline.mae_p50 AS DOUBLE), 1) AS baseline_mae_days,
       round(try_cast(summary:main_prior_review.gbt.mae_p50 AS DOUBLE), 1) AS model_mae_days,
       round(try_cast(summary:main_prior_review.gbt.coverage_p80 AS DOUBLE), 3) AS p80_coverage,
       summary:registered.version::string AS registered_version,
       summary:registered.promoted::string AS promoted_to_champion
FROM procurement.ml.duration_model_report
UNION ALL
SELECT 'Post review (secondary)',
       try_cast(summary:secondary_post_review.n_test AS INT),
       summary:secondary_post_review.test_from::string,
       round(try_cast(summary:secondary_post_review.baseline.mae_p50 AS DOUBLE), 1),
       round(try_cast(summary:secondary_post_review.gbt.mae_p50 AS DOUBLE), 1),
       round(try_cast(summary:secondary_post_review.gbt.coverage_p80 AS DOUBLE), 3),
       CAST(NULL AS STRING), CAST(NULL AS STRING)
FROM procurement.ml.duration_model_report
;
-- 2b. Re-bid / cancellation rates (notebook 51 labels), with and without P157054 (one project dominates re-advertisements)
CREATE OR REPLACE VIEW procurement.ops.kpi_rebid_rates AS
SELECT scope, count(*) AS tenders,
       round(avg(CASE WHEN rebid THEN 1 ELSE 0 END), 4) AS rebid_rate,
       round(avg(CASE WHEN re_advertised_new_ref THEN 1 ELSE 0 END), 4) AS re_advertised_new_ref_rate,
       round(avg(CASE WHEN cancellation THEN 1 ELSE 0 END), 4) AS cancellation_rate,
       round(avg(CASE WHEN rebid_or_cancellation THEN 1 ELSE 0 END), 4) AS rebid_or_cancellation_rate
FROM (SELECT 'All road tenders' AS scope, * FROM procurement.gold.rebid_labels
      UNION ALL
      SELECT 'Excluding P157054' AS scope, * FROM procurement.gold.rebid_labels WHERE project_id <> 'P157054')
GROUP BY scope
;
-- 3. Assistant quality (notebook 61): one row per evaluation run, TEST half only
CREATE OR REPLACE VIEW procurement.ops.kpi_eval_runs AS
SELECT run_ts,
       try_cast(summary:n_test AS INT) AS n_test,
       round(try_cast(summary:overall_test['recall@5'] AS DOUBLE), 3) AS recall5_strict,
       round(try_cast(summary:overall_test_lenient['recall@5'] AS DOUBLE), 3) AS recall5_lenient,
       round(try_cast(summary:overall_test.mrr AS DOUBLE), 3) AS mrr_strict,
       round(try_cast(summary:overall_test.citation_validity AS DOUBLE), 3) AS citation_validity,
       round(try_cast(summary:overall_test.claim_support_rate AS DOUBLE), 3) AS claim_support,
       round(try_cast(summary:overall_test.refusal_accuracy AS DOUBLE), 3) AS refusal_accuracy,
       round(try_cast(summary:overall_test.answered_share AS DOUBLE), 3) AS answered_share,
       try_cast(summary:guardrail_removals_test.mechanical AS INT) AS claims_removed_mechanical,
       coalesce(try_cast(summary:guardrail_removals_test.verifier.unsupported AS INT), 0)
         + coalesce(try_cast(summary:guardrail_removals_test.verifier.no_verdict AS INT), 0) AS claims_removed_verifier,
       try_cast(summary:guardrail_removals_test.verifier.verified AS INT) AS claims_verified,
       try_cast(summary:gate_calibrated_on_calibration_half.threshold AS DOUBLE) AS gate_threshold,
       summary:retrieval_path::string AS retrieval_path
FROM procurement.ml.rag_eval_results
;
-- 4a. Usage (app request log): TEST USAGE ONLY until a pilot
CREATE OR REPLACE VIEW procurement.ops.kpi_usage_daily AS
SELECT to_date(ts) AS day, page, count(*) AS requests, count(DISTINCT user) AS users,
       round(percentile(try_cast(payload:timings.total_s AS DOUBLE), 0.5), 2) AS latency_p50_s,
       round(percentile(try_cast(payload:timings.total_s AS DOUBLE), 0.9), 2) AS latency_p90_s,
       sum(CASE WHEN payload:timings.llm_s IS NOT NULL THEN 1 ELSE 0 END)
         + sum(CASE WHEN payload:timings.verifier_s IS NOT NULL THEN 1 ELSE 0 END) AS llm_calls,
       sum(CASE WHEN payload:status::string = 'refused_no_evidence' THEN 1 ELSE 0 END) AS refused_by_gate
FROM procurement.ops.rag_requests GROUP BY to_date(ts), page
;
-- 4b. Feedback (app): TEST USAGE ONLY until a pilot
CREATE OR REPLACE VIEW procurement.ops.kpi_feedback_daily AS
SELECT to_date(ts) AS day, page,
       sum(CASE WHEN vote = 'up' THEN 1 ELSE 0 END) AS thumbs_up,
       sum(CASE WHEN vote = 'down' THEN 1 ELSE 0 END) AS thumbs_down,
       sum(CASE WHEN comment <> '' THEN 1 ELSE 0 END) AS wrong_source_comments
FROM procurement.ops.feedback GROUP BY to_date(ts), page
;
-- 4c. Business KPIs: PROPOSED pilot targets copied from docs/08 sec. 6.2-6.3 (no measured values exist yet)
CREATE OR REPLACE VIEW procurement.ops.kpi_proposed_targets AS
SELECT * FROM VALUES
  ('Adoption: weekly active pilot users', 'online', '>= 60% of ~10 pilot specialists weekly active', 'Phase 1 shadow pilot exit criterion', 'docs/08 sec. 6.2', 'PROPOSED, not measured'),
  ('Answers flagged wrong-source', 'online', '<= 5% of answers', 'Phase 1 shadow pilot exit criterion', 'docs/08 sec. 6.2', 'PROPOSED, not measured'),
  ('Unsupported claims in audit', 'online', '0 in a 100-answer audit', 'Phase 1 shadow pilot exit criterion', 'docs/08 sec. 6.2', 'PROPOSED, not measured'),
  ('Planning cycle time (procurement plan drafting)', 'online', 'before/after timing in pilot teams (no numeric target set)', 'Phase 1 KPI', 'docs/08 sec. 6.3', 'PROPOSED, not measured'),
  ('Forecast accuracy in use', 'online', 'predicted P50 vs actual signing for pilot tenders (no numeric target set)', 'Phase 1 KPI', 'docs/08 sec. 6.3', 'PROPOSED, not measured'),
  ('Answers accepted without edit, feedback rate', 'online', 'from ops.rag_requests and ops.feedback (no numeric target set)', 'Phase 1 KPI', 'docs/08 sec. 6.3', 'PROPOSED, not measured'),
  ('Guardrail removal rate', 'monitoring', 'removed / total claims per week (no numeric target set)', 'Monitoring KPI', 'docs/08 sec. 6.3', 'PROPOSED, not measured'),
  ('Retrieval recall@5', 'offline', '>= 0.7', 'Phase 0 exit criterion', 'docs/08 sec. 6.2', 'measured: see Assistant quality'),
  ('Claim support after guardrails', 'offline', '>= 0.9', 'Phase 0 exit criterion', 'docs/08 sec. 6.2', 'measured: see Assistant quality'),
  ('Duration P50 vs baseline; P80 coverage', 'offline', 'P50 beats baseline; P80 coverage 0.7-0.9', 'Phase 0 exit criterion', 'docs/08 sec. 6.2', 'measured: see Model quality'),
  ('Weekly refresh job duration', 'operations', '<= 4 h', 'Phase 2 expansion exit criterion', 'docs/08 sec. 6.2', 'PROPOSED, not measured')
AS t(kpi, kind, target, phase, source, status)
