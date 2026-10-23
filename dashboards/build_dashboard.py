"""Builds dashboards/procurement_kpis.lvdash.json, the AI/BI (Lakeview) KPI dashboard, from plain Python.

Plain-language summary: a Lakeview dashboard is a JSON file with DATASETS (SQL queries) and PAGES (widgets placed on a
6-column grid). Writing that JSON by hand is error-prone, so this script describes each widget in one line and writes
the file. Every dataset reads one of the small views in dashboards/kpi_views.sql (no numbers are typed in here).
Run:  python dashboards/build_dashboard.py      (then notebooks/85_kpi_dashboard.py deploys and publishes it)
"""
import json
import os

V = "procurement.ops"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procurement_kpis.lvdash.json")

# name -> (display name, SQL, {column: type}); type is one of string / integer / float / datetime
DATASETS = {
    "source_refresh": ("Last refresh per source", f"SELECT * FROM {V}.kpi_source_refresh",
                       {"source": "string", "layer": "string", "last_refresh": "datetime", "row_count": "integer"}),
    "dq_runs": ("Data-quality gate runs", f"SELECT * FROM {V}.kpi_dq_runs ORDER BY run_ts DESC",
                {"run_ts": "datetime", "checks": "integer", "passed": "integer", "failed": "integer", "gate": "string"}),
    "dq_latest": ("Data-quality gate, latest run",
                  f"SELECT failed AS failed_checks, passed AS passed_checks FROM {V}.kpi_dq_runs ORDER BY run_ts DESC LIMIT 1",
                  {"failed_checks": "integer", "passed_checks": "integer"}),
    "coverage_fy": ("Contract coverage by FY", f"SELECT * FROM {V}.kpi_coverage_fy ORDER BY fiscal_year",
                    {"fiscal_year": "integer", "coverage_status": "string", "n_contracts": "integer",
                     "award_notices": "integer", "matched": "integer", "coverage_share": "float"}),
    "missing_rates": ("Missing-value rates", f"SELECT * FROM {V}.kpi_missing_rates ORDER BY source_dataset",
                      {"source_dataset": "string", "contracts": "integer", "signing_date_missing": "float",
                       "amount_missing": "float", "category_missing": "float", "method_missing": "float",
                       "review_type_unknown": "float", "country_missing": "float"}),
    "duration_model": ("Duration model vs baseline", f"SELECT * FROM {V}.kpi_duration_model",
                       {"population": "string", "n_test": "integer", "test_from": "string",
                        "baseline_mae_days": "float", "model_mae_days": "float", "p80_coverage": "float",
                        "registered_version": "string", "promoted_to_champion": "string"}),
    "duration_main": ("Duration model, prior review (champion)",
                      f"SELECT model_mae_days, baseline_mae_days, p80_coverage FROM {V}.kpi_duration_model "
                      f"WHERE population = 'Prior review (main)'",
                      {"model_mae_days": "float", "baseline_mae_days": "float", "p80_coverage": "float"}),
    "rebid_rates": ("Re-bid or cancellation rates", f"SELECT * FROM {V}.kpi_rebid_rates ORDER BY scope",
                    {"scope": "string", "tenders": "integer", "rebid_rate": "float",
                     "re_advertised_new_ref_rate": "float", "cancellation_rate": "float",
                     "rebid_or_cancellation_rate": "float"}),
    "eval_runs": ("Evaluation runs", f"SELECT * FROM {V}.kpi_eval_runs ORDER BY run_ts DESC",
                  {"run_ts": "datetime", "n_test": "integer", "recall5_strict": "float", "recall5_lenient": "float",
                   "mrr_strict": "float", "citation_validity": "float", "claim_support": "float",
                   "refusal_accuracy": "float", "answered_share": "float", "claims_removed_mechanical": "integer",
                   "claims_removed_verifier": "integer", "claims_verified": "integer", "gate_threshold": "float",
                   "retrieval_path": "string"}),
    "eval_latest": ("Evaluation, latest run",
                    f"SELECT recall5_strict, recall5_lenient, citation_validity, claim_support, refusal_accuracy, "
                    f"claims_removed_mechanical + claims_removed_verifier AS claims_removed "
                    f"FROM {V}.kpi_eval_runs ORDER BY run_ts DESC LIMIT 1",
                    {"recall5_strict": "float", "recall5_lenient": "float", "citation_validity": "float",
                     "claim_support": "float", "refusal_accuracy": "float", "claims_removed": "integer"}),
    "eval_trend": ("Recall@5 trend",
                   f"SELECT run_ts, 'strict (gold passage)' AS measure, recall5_strict AS recall_at_5 FROM {V}.kpi_eval_runs "
                   f"UNION ALL SELECT run_ts, 'lenient (any passage with the answer)', recall5_lenient FROM {V}.kpi_eval_runs "
                   f"WHERE recall5_lenient IS NOT NULL",
                   {"run_ts": "datetime", "measure": "string", "recall_at_5": "float"}),
    "usage_daily": ("Requests per day (test usage)", f"SELECT * FROM {V}.kpi_usage_daily ORDER BY day DESC",
                    {"day": "datetime", "page": "string", "requests": "integer", "users": "integer",
                     "latency_p50_s": "float", "latency_p90_s": "float", "llm_calls": "integer",
                     "refused_by_gate": "integer"}),
    "usage_totals": ("Usage totals (test usage)",
                     f"SELECT (SELECT coalesce(sum(requests), 0) FROM {V}.kpi_usage_daily) AS requests, "
                     f"(SELECT coalesce(sum(llm_calls), 0) FROM {V}.kpi_usage_daily) AS llm_calls, "
                     f"(SELECT coalesce(sum(thumbs_up), 0) FROM {V}.kpi_feedback_daily) AS thumbs_up, "
                     f"(SELECT coalesce(sum(thumbs_down), 0) FROM {V}.kpi_feedback_daily) AS thumbs_down",
                     {"requests": "integer", "llm_calls": "integer", "thumbs_up": "integer", "thumbs_down": "integer"}),
    "feedback_daily": ("Feedback per day (test usage)", f"SELECT * FROM {V}.kpi_feedback_daily ORDER BY day DESC",
                       {"day": "datetime", "page": "string", "thumbs_up": "integer", "thumbs_down": "integer",
                        "wrong_source_comments": "integer"}),
    "targets": ("Proposed pilot targets", f"SELECT * FROM {V}.kpi_proposed_targets",
                {"kpi": "string", "kind": "string", "target": "string", "phase": "string", "source": "string",
                 "status": "string"}),
}

_DISPLAY = {"string": "string", "integer": "number", "float": "number", "datetime": "datetime"}
_n = [0]


def _name(prefix):
    _n[0] += 1
    return f"{prefix}_{_n[0]:02d}"


def _query(ds, cols):
    return [{"name": "main_query", "query": {"datasetName": ds, "disaggregated": True,
                                             "fields": [{"name": c, "expression": f"`{c}`"} for c in cols]}}]


def _frame(title, desc=None):
    f = {"showTitle": True, "title": title}
    if desc:
        f.update(showDescription=True, description=desc)
    return f


def text(lines, x, y, w, h):
    return {"widget": {"name": _name("text"), "multilineTextboxSpec": {"lines": lines}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def table(ds, title, x, y, w, h, desc=None):
    cols = DATASETS[ds][2]
    spec_cols = [{"fieldName": c, "displayName": c, "title": c, "type": t, "displayAs": _DISPLAY[t], "visible": True,
                  "order": i, "alignContent": "left" if t == "string" else "right", "allowSearch": False,
                  "booleanValues": ["false", "true"], "imageUrlTemplate": "{{ @ }}", "imageTitleTemplate": "{{ @ }}",
                  "imageWidth": "", "imageHeight": "", "linkUrlTemplate": "{{ @ }}", "linkTextTemplate": "{{ @ }}",
                  "linkTitleTemplate": "{{ @ }}", "linkOpenInNewTab": True, "allowHTML": False,
                  "highlightLinks": False, "useMonospaceFont": False, "preserveWhitespace": False,
                  **({"numberFormat": "0.000"} if t == "float" else {}),
                  **({"dateTimeFormat": "YYYY-MM-DD HH:mm"} if t == "datetime" else {})}
                 for i, (c, t) in enumerate(cols.items())]
    return {"widget": {"name": _name("table"), "queries": _query(ds, list(cols)),
                       "spec": {"version": 1, "widgetType": "table", "encodings": {"columns": spec_cols},
                                "invisibleColumns": [], "allowHTMLByDefault": False, "itemsPerPage": 25,
                                "paginationSize": "default", "condensed": True, "withRowNumber": False,
                                "frame": _frame(title, desc)}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def counter(ds, field, title, x, y, w=1, h=3, desc=None):
    return {"widget": {"name": _name("counter"), "queries": _query(ds, [field]),
                       "spec": {"version": 2, "widgetType": "counter",
                                "encodings": {"value": {"fieldName": field, "displayName": title}},
                                "frame": _frame(title, desc)}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def chart(kind, ds, xf, yf, title, x, y, w, h, color=None, x_scale="categorical", desc=None):
    enc = {"x": {"fieldName": xf, "scale": {"type": x_scale}, "displayName": xf},
           "y": {"fieldName": yf, "scale": {"type": "quantitative"}, "displayName": yf}}
    cols = [xf, yf]
    if color:
        enc["color"] = {"fieldName": color, "scale": {"type": "categorical"}, "displayName": color}
        cols.append(color)
    return {"widget": {"name": _name(kind), "queries": _query(ds, cols),
                       "spec": {"version": 3, "widgetType": kind, "encodings": enc, "frame": _frame(title, desc)}},
            "position": {"x": x, "y": y, "width": w, "height": h}}


def build():
    _n[0] = 0
    health = [
        text(["## Data health",
              "Coverage is **partial, not missing** before FY2020: a zero or a low count in a partial year is *not* "
              "evidence of no activity. Coverage = share of published Contract Award notices matched in the contract "
              "data. The data-quality gate is notebook 12; models do not run on a refresh that failed it."], 0, 0, 6, 2),
        table("source_refresh", "Last refresh and row count per source", 0, 2, 4, 6),
        counter("dq_latest", "failed_checks", "Failed checks (latest gate run)", 4, 2, 1, 3),
        counter("dq_latest", "passed_checks", "Passed checks (latest gate run)", 5, 2, 1, 3),
        table("dq_runs", "Data-quality gate history", 4, 5, 2, 3),
        chart("bar", "coverage_fy", "fiscal_year", "coverage_share", "Contract coverage by fiscal year", 0, 8, 3, 6,
              color="coverage_status", desc="Share of published award notices matched in the contract data"),
        table("coverage_fy", "Coverage by fiscal year (status label on every row)", 3, 8, 3, 6),
        table("missing_rates", "Missing-value rates, road-sector contracts, per dataset", 0, 14, 6, 4,
              desc="review_type 'unknown' is stored, never guessed (DS01004 has no review-type field)"),
    ]
    models = [
        text(["## Model quality",
              "Duration model: quantile GBT vs a median baseline on the latest time slice (held out). Main population: "
              "prior-review contracts. Re-bid or cancellation rates are shown with and without project P157054, whose "
              "batch re-advertisements dominate the count."], 0, 0, 6, 2),
        counter("duration_main", "model_mae_days", "Model MAE (days), prior review", 0, 2, 2, 3),
        counter("duration_main", "baseline_mae_days", "Baseline MAE (days), prior review", 2, 2, 2, 3),
        counter("duration_main", "p80_coverage", "P80 coverage (target 0.7-0.9)", 4, 2, 2, 3),
        table("duration_model", "Duration model vs baseline (latest report; champion version)", 0, 5, 6, 4),
        chart("bar", "rebid_rates", "scope", "rebid_or_cancellation_rate", "Re-bid or cancellation rate", 0, 9, 2, 6),
        table("rebid_rates", "Re-bid, re-advertisement and cancellation rates", 2, 9, 4, 6),
    ]
    assistant = [
        text(["## Assistant quality",
              "Frozen evaluation set, **test half only** (the refusal threshold is calibrated on the other half). "
              "Strict recall counts only the gold passage; lenient recall counts any retrieved passage an LLM judge "
              "finds contains the answer. Claims removed = mechanical citation checks + claim-support verifier."],
             0, 0, 6, 2),
        counter("eval_latest", "recall5_strict", "Recall@5 strict", 0, 2, 1, 3),
        counter("eval_latest", "recall5_lenient", "Recall@5 lenient", 1, 2, 1, 3),
        counter("eval_latest", "citation_validity", "Citation validity", 2, 2, 1, 3),
        counter("eval_latest", "claim_support", "Claim support", 3, 2, 1, 3),
        counter("eval_latest", "claims_removed", "Claims removed by guardrails", 4, 2, 1, 3),
        counter("eval_latest", "refusal_accuracy", "Refusal accuracy", 5, 2, 1, 3),
        chart("line", "eval_trend", "run_ts", "recall_at_5", "Recall@5 across evaluation runs", 0, 5, 6, 5,
              color="measure", x_scale="temporal"),
        table("eval_runs", "All evaluation runs", 0, 10, 6, 5),
    ]
    usage = [
        text(["## Usage & impact: TEST USAGE ONLY",
              "**These are developer test requests, not users.** The app has not been piloted; usage and feedback "
              "numbers are meaningful only after a pilot starts. LLM calls = answer + claim verifier per request.",
              "Business KPIs below are **PROPOSED pilot targets** copied from the proposal (docs/08 sec. 6.2-6.3); "
              "no measured values exist for them yet."], 0, 0, 6, 3),
        counter("usage_totals", "requests", "Requests (test usage)", 0, 3, 1, 3),
        counter("usage_totals", "llm_calls", "LLM calls (test usage)", 1, 3, 1, 3),
        counter("usage_totals", "thumbs_up", "👍 (test usage)", 2, 3, 1, 3),
        counter("usage_totals", "thumbs_down", "👎 (test usage)", 3, 3, 1, 3),
        chart("bar", "usage_daily", "day", "requests", "Requests per day (test usage only)", 0, 6, 3, 5,
              color="page", x_scale="temporal"),
        table("usage_daily", "Requests, latency and LLM calls per day (test usage only)", 3, 6, 3, 5),
        table("feedback_daily", "Feedback per day (test usage only)", 0, 11, 6, 4),
        table("targets", "Business KPIs: PROPOSED pilot targets (not measured)", 0, 15, 6, 6),
    ]
    pages = [("health", "1. Data health", health), ("models", "2. Model quality", models),
             ("assistant", "3. Assistant quality", assistant), ("usage", "4. Usage & impact", usage)]
    return {
        "datasets": [{"name": k, "displayName": v[0], "queryLines": [v[1]]} for k, v in DATASETS.items()],
        "pages": [{"name": n, "displayName": d, "layout": lay, "pageType": "PAGE_TYPE_CANVAS"} for n, d, lay in pages],
    }


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(build(), f, indent=1, ensure_ascii=False)
    print("wrote", OUT)
