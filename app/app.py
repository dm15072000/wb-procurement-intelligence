"""Procurement Intelligence: Databricks App (Streamlit). Design: docs/03_model_rag_design.md §8.

Plain-language summary: four screens.
  1. Plan a package (L1, L2): similar past contracts, a predicted duration range, the re-bid or cancellation history of similar
     tenders, an amendment indicator, and a cited brief of risks and lessons from past project documents.
  2. Monitor a project (L3): rating timeline with ISR/ICR/IEG conflicts, early-warning flags, disbursement, contracts.
  3. Ask the evidence (L4): a free question answered only from cited passages.
  4. Data coverage: what the data does and does not cover (partial years, missing CPIA).
Every number comes from SQL or a model, never from the language model; every LLM claim cites a source and has
passed the guardrails in src/guardrails/citation_check.py. Feedback goes to procurement.ops.feedback.
"""
import json
import os
import sys

# Databricks Apps authenticate with OAuth (the app's service principal); MLflow uses it only with this flag set.
os.environ.setdefault("MLFLOW_ENABLE_DB_SDK", "true")
from datetime import date, datetime, timezone

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.models import duration as du               # noqa: E402
from src.rag import databricks_tools as dt          # noqa: E402
from src.rag import pipeline as pl                  # noqa: E402
from src.risk import amendment as am                # noqa: E402

CATALOG = dt.CATALOG
st.set_page_config(page_title="Procurement Intelligence", layout="wide")


# --- connections (cached for the app's lifetime) ----------------------------------------------------------------------

@st.cache_resource
def connection():
    from databricks import sql
    from databricks.sdk.core import Config
    cfg = Config()
    return sql.connect(server_hostname=cfg.host,
                       http_path=f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}",
                       credentials_provider=lambda: cfg.authenticate)


def _plain(v):
    """The SQL connector returns ARRAY columns as numpy arrays, whose truth value is ambiguous (`arr or []` raises).
    Convert them (and nested ones) to plain Python lists so app and pipeline code can treat rows as ordinary data."""
    if hasattr(v, "tolist") and not isinstance(v, (str, bytes)):
        v = v.tolist()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def run_sql(query, params=None):
    with connection().cursor() as cur:
        cur.execute(query, parameters=params or {})
        cols = [c[0] for c in cur.description] if cur.description else []
        return [{c: _plain(x) for c, x in zip(cols, r)} for r in cur.fetchall()] if cols else []


@st.cache_resource
def backend():
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    idx, fallback = None, None
    try:
        from databricks.vector_search.client import VectorSearchClient
        idx = VectorSearchClient(disable_notice=True).get_index(dt.VS_ENDPOINT, dt.VS_INDEX)
        idx.describe()
    except Exception:
        idx = None
        rows = run_sql(f"SELECT {', '.join(dt.rt.COLUMNS)}, doc_date_int, is_primary, embedding "
                       f"FROM {CATALOG}.docs.chunk_embeddings")
        fallback = dt.fallback_rows_from(rows)
    model, info = None, {}
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
        mlflow.set_registry_uri("databricks-uc")
        model = mlflow.pyfunc.load_model(dt.DURATION_MODEL)
        mv = MlflowClient().get_model_version_by_alias(f"{CATALOG}.ml.tender_duration", "champion")
        info = {"population": "prior-review", "n_train": mv.tags.get("n_train"), "mae_p50": mv.tags.get("heldout_mae_p50")}
        if model is None:
            info["load_error"] = "the model registry returned no model"
        else:
            info["version"] = mv.version
            print(f"duration model loaded: {dt.DURATION_MODEL} version {mv.version}", file=sys.stderr, flush=True)
    except Exception as e:      # shown on the Plan screen, so a missing model is never silent
        info = {"load_error": f"{type(e).__name__}: {str(e)[:200]}"}
        print("duration model not loaded:", repr(e), file=sys.stderr, flush=True)
    return dt.DatabricksBackend(run_sql, w, vs_index=idx, fallback_rows=fallback, duration_model=model,
                                duration_model_info=info), ("Vector Search" if idx else "cosine fallback")


@st.cache_data(ttl=3600)
def min_score():
    rows = run_sql(f"SELECT value FROM {CATALOG}.ops.config WHERE key = 'rag_min_score'")
    return float(rows[0]["value"]) if rows else 0.55


@st.cache_data(ttl=3600)
def kpi_dashboard_url():
    """Link to the AI/BI KPI dashboard, saved in ops.config by notebook 85 (None until it has run)."""
    try:
        rows = run_sql(f"SELECT value FROM {CATALOG}.ops.config WHERE key = 'kpi_dashboard_url'")
        return rows[0]["value"] if rows else None
    except Exception:
        return None


@st.cache_data(ttl=3600)
def options():
    countries = run_sql(f"""SELECT DISTINCT c.borrower_country_iso3 AS iso3, n.name FROM {CATALOG}.silver.contracts c
                            JOIN {CATALOG}.silver.countries n ON c.borrower_country_iso3 = n.iso3
                            WHERE c.in_universe ORDER BY n.name""")
    cats = run_sql(f"""SELECT procurement_category AS cat, procurement_method AS method, count(*) AS n
                       FROM {CATALOG}.silver.contracts WHERE in_universe AND procurement_category IS NOT NULL
                       GROUP BY 1, 2 ORDER BY n DESC""")
    projects = run_sql(f"""SELECT project_id, project_name, country_iso3_list, in_slice, status
                           FROM {CATALOG}.silver.projects WHERE in_universe ORDER BY in_slice DESC, project_id""")
    return countries, cats, projects


def tools(log_page):
    b, _ = backend()
    return pl.Tools(embed=b.embed, search=b.search, llm=b.chat, similar_contracts=b.similar_contracts,
                    predictions=b.predictions, related_projects=b.related_projects, describes_hits=b.describes_hits,
                    keyword_search=b.keyword_search,   # [] when Vector Search HYBRID mode covers keywords
                    translated_keyword_search=b.translated_keyword_search, translate_query=b.translate_to_french,
                    resolve_projects=b.resolve_projects, rating_rows=b.rating_rows,
                    verify_claims=True, min_score=min_score(), log=lambda rec: log_request(log_page, rec))


def user_email():
    try:
        return st.context.headers.get("X-Forwarded-Email") or "unknown"
    except Exception:
        return "unknown"


def log_request(page, rec):
    try:
        run_sql(f"INSERT INTO {CATALOG}.ops.rag_requests VALUES (:ts, :user, :page, :payload)",
                {"ts": datetime.now(timezone.utc).isoformat(), "user": user_email(), "page": page,
                 "payload": json.dumps(rec, default=str)[:20000]})
    except Exception:
        pass   # logging must never break the answer


def feedback(page, result, key):
    c1, c2, c3 = st.columns([1, 1, 4])
    vote = None
    if c1.button("👍 Useful", key=f"up{key}"):
        vote = "up"
    if c2.button("👎 Not useful", key=f"down{key}"):
        vote = "down"
    wrong = c3.text_input("Wrong or missing source? (optional: which one, and why)", key=f"fb{key}")
    if vote or (wrong and c3.button("Send", key=f"send{key}")):
        run_sql(f"INSERT INTO {CATALOG}.ops.feedback VALUES (:ts, :user, :page, :status, :vote, :comment, :chunks)",
                {"ts": datetime.now(timezone.utc).isoformat(), "user": user_email(), "page": page,
                 "status": result.status, "vote": vote or "comment", "comment": wrong or "",
                 "chunks": json.dumps(result.retrieved)})
        st.success("Thank you: feedback saved.")


def show_result(result, page, key):
    """Claims with citation badges, removed-claim count, and the sources panel."""
    if result.status == "refused_no_evidence":
        st.warning("No sufficiently relevant passages were found, so no brief is given (retrieval gate).")
    elif result.status == "refused_unparseable":
        st.warning("The model's reply could not be verified, so it is not shown.")
    elif result.status == "insufficient_evidence":
        st.info("The sources found do not support an answer.")
    for c in result.claims:
        st.markdown(f"- {c['text']}  " + " ".join(f"`{x}`" for x in c["citations"]))
    rep = result.guardrail_report or {}
    if rep.get("removed"):
        ver = rep.get("verifier") or {}
        n_ver = ver.get("unsupported", 0) + ver.get("no_verdict", 0)
        st.caption(f"Guardrails removed {rep['removed']} of {rep['claims']} claims: {rep['removed'] - n_ver} failed the "
                   f"citation checks (no source, unknown source, or a number / project ID not in the cited source) and "
                   f"{n_ver} were judged not supported by their cited text by the verifier.")
    if result.injection_flags:
        st.caption(f"{len(result.injection_flags)} passage(s) contained instruction-like text and were not shown to the model.")
    with st.expander(f"Sources ({len(result.evidence)} passages, {len(result.facts)} facts)"):
        for eid, h in result.evidence.items():
            link = f" — [PDF]({h['pdf_url']})" if h.get("pdf_url") else ""
            st.markdown(f"**{eid}** · {h.get('doc_type')} · {h.get('doc_date')} · {h.get('project_id') or ''} · "
                        f"section *{(h.get('section_path') or '')[:100]}* · p.{h.get('page_start')}{link}")
            st.caption((h.get("text") or "")[:600])
        for fid, f in result.facts.items():
            st.markdown(f"**{fid}** · {f['text']}")
    feedback(page, result, key)


# --- screens ----------------------------------------------------------------------------------------------------------

def plan_screen():
    st.header("Plan a procurement package")
    st.caption("Road projects (Transport – Rural and Inter-Urban Roads). Durations are mainly valid for prior-review "
               "contracts. Similar contracts show the data's coverage status: a missing contract in a partial year is "
               "not evidence that nothing happened.")
    countries, cats, projects = options()
    c1, c2, c3 = st.columns(3)
    country = c1.selectbox("Country", countries, format_func=lambda r: r["name"])
    in_country = [p for p in projects if country and country["iso3"] in (p["country_iso3_list"] or [])]
    project = c2.selectbox("Project (optional)", [None] + in_country,
                           format_func=lambda p: "— none —" if p is None else f"{p['project_id']} {p['project_name'][:50]}")
    category = c3.selectbox("Category", sorted({c["cat"] for c in cats}))
    methods = [c["method"] for c in cats if c["cat"] == category and c["method"]]
    c4, c5, c6 = st.columns(3)
    method = c4.selectbox("Method (optional)", [None] + methods, format_func=lambda m: m or "— any —")
    amount = c5.number_input("Estimated value (USD)", min_value=0.0, value=2_000_000.0, step=100_000.0)
    as_of = c6.date_input("Planning date (only documents published by then are used)", value=date.today())
    scope = st.text_area("Scope of the package", "Rehabilitation of rural roads including bridges and drainage")
    if not st.button("Prepare brief", type="primary"):
        return
    pkg = {"country_iso3": country["iso3"], "country_name": country["name"],
           "project_id": project["project_id"] if project else None, "category": category, "method": method,
           "amount_usd": amount or None, "scope": scope, "as_of": str(as_of)}
    with st.spinner("Finding precedents, predicting, retrieving and checking…"):
        result = pl.run(pkg, tools("plan"), task="plan")
    facts = list(result.facts.values())
    dur = next((f for f in facts if f.get("kind") == "duration_model"), None)
    reb = next((f for f in facts if f.get("kind") == "rebid_rate"), None)
    m1, m2, m3 = st.columns(3)
    if dur:
        m1.metric("Predicted notice → signing", f"{dur['p50']} days", help=dur["text"])
        m1.caption(du.days_to_text(dur["p20"], dur["p50"], dur["p80"]))
    else:
        err = (backend()[0].duration_model_info or {}).get("load_error")
        m1.info("Duration model not available." + (f" Reason: {err}" if err else " No prediction for this package."))
    if reb:
        m2.metric("Re-bid or cancellation rate of similar tenders", f"{100 * reb['rate']:.0f}%", help=reb["text"])
        m2.caption(reb["text"])
    b, _ = backend()
    iso3, proj_ctx, _, gov, _ = b._context(pkg)
    g = du.governance_asof(gov, iso3, du.as_date(pkg["as_of"]))
    p75 = run_sql(f"""SELECT percentile(total_amount_usd, 0.75) AS p FROM {CATALOG}.silver.contracts
                      WHERE in_universe AND procurement_category = :c""", {"c": category})
    isr = run_sql(f"""SELECT dimension, score FROM {CATALOG}.gold.ratings WHERE project_id = :pid
                      AND doc_type = 'Implementation Status and Results Report' AND doc_date <= :d
                      AND dimension IN ('implementation', 'procurement') ORDER BY doc_date""",
                  {"pid": pkg["project_id"] or "", "d": pkg["as_of"]})
    latest = {r["dimension"]: r["score"] for r in isr}
    ev = run_sql(f"""SELECT count(*) AS n FROM {CATALOG}.silver.contract_events WHERE project_id = :pid AND doc_date <= :d
                     AND event_type IN ('amendment', 'time_extension', 'cost_overrun')""",
                 {"pid": pkg["project_id"] or "", "d": pkg["as_of"]}) if pkg["project_id"] else None
    s = am.score({"category": category, "amount_usd": amount or None, "category_p75_usd": p75[0]["p"] if p75 else None,
                  "p80_days": dur["p80"] if dur else None, "wgi_ge": g.get("wgi_ge"),
                  "latest_isr_min_score": min(latest.values()) if latest else None,
                  "n_earlier_amendment_events": ev[0]["n"] if ev else None})
    m3.metric("Amendment indicator (heuristic)", f"{s['score']} / {s['max_known']}")
    m3.caption("Heuristic only, not a probability: it did not track the amendment events extracted from reports "
               "(Spearman r = -0.08, notebook 70). " + "; ".join(f["reason"] for f in s["factors"]) +
               (f" Unknown: {', '.join(s['unknown'])}." if s["unknown"] else ""))
    opts = [f for f in facts if f.get("kind") == "method_option"]
    if opts:
        st.subheader("Market approach options: how similar packages were procured")
        st.dataframe(pd.DataFrame([{"method": f["method"], "market approach": f["market"], "contracts": f["n"],
                                    "share": f"{100 * f['share']:.0f}%" if f["share"] is not None else "",
                                    "median notice → signing (days)": f["median_days"]} for f in opts]),
                     hide_index=True, use_container_width=True)
        st.caption("Descriptive history from road projects (same category, 0.3–3x the value), not a recommendation; "
                   "durations only where a notice matched the contract.")
    st.subheader("Similar past contracts")
    sims = [f for f in facts if f.get("kind") == "similar_contract"]
    if sims:
        st.dataframe(pd.DataFrame([{"fact": fid, "contract": f["contract_key"], "project": f["project_id"],
                                    "source": f["source_dataset"], "coverage": f["coverage_status"]}
                                   for fid, f in result.facts.items() if f.get("kind") == "similar_contract"]),
                     hide_index=True, use_container_width=True)
    else:
        st.caption("No similar contracts in the data for this category and value range.")
    st.subheader("Risks, mitigations and lessons from past documents")
    show_result(result, "plan", "plan")


def monitor_screen():
    st.header("Monitor a project")
    _, _, projects = options()
    p = st.selectbox("Project", projects, format_func=lambda p: f"{p['project_id']} {p['project_name'][:60]}"
                     + (" (demo slice)" if p["in_slice"] else ""))
    if not p:
        return
    pid = p["project_id"]
    try:   # development objective and financiers (notebook 13); skipped quietly if not built yet
        pdo = run_sql(f"SELECT pdo FROM {CATALOG}.silver.project_pdo WHERE project_id = :p", {"p": pid})
        fin = run_sql(f"""SELECT financier, financier_type, loan_code, is_cofinancing
                           FROM {CATALOG}.silver.project_financing WHERE project_id = :p""", {"p": pid})
    except Exception:
        pdo, fin = [], []
    if pdo and pdo[0]["pdo"]:
        st.markdown(f"**Development objective:** {pdo[0]['pdo']}")
    if fin:
        co = [f for f in fin if f["is_cofinancing"]]
        st.caption("Financing: " + "; ".join(f"{f['financier']} ({f['financier_type']}, {f['loan_code']})" for f in fin)
                   + (f" · co-financing from {len(co)} source(s)" if co else " · no co-financing recorded"))
    flags = run_sql(f"SELECT flag, reason, evidence FROM {CATALOG}.gold.early_warnings WHERE project_id = :p", {"p": pid})
    st.subheader("Early-warning flags")
    st.caption("A flag means CHECK, not LATE: an overdue tender may also be a contract that did not match its notice "
               "(about a third of contracts match a notice).")
    if flags:      # a plain if/else: a one-line conditional EXPRESSION is shown by Streamlit "magic" and crashes
        st.dataframe(pd.DataFrame(flags), hide_index=True, use_container_width=True)
    else:
        st.caption("No flags.")
    st.subheader("Ratings over time (ISR → ICR → IEG)")
    rat = run_sql(f"""SELECT doc_date, doc_type, dimension, value, matched FROM {CATALOG}.gold.ratings
                      WHERE project_id = :p ORDER BY doc_date""", {"p": pid})
    if rat:
        df = pd.DataFrame(rat)
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.caption("No extracted ratings (documents are parsed for the 16 demo-slice projects only).")
    conf = run_sql(f"SELECT dimension, flag, detail, chosen_value, chosen_source, rule FROM {CATALOG}.gold.rating_conflicts "
                   f"WHERE project_id = :p", {"p": pid})
    if conf:
        st.warning("Conflicting evidence: both sides are shown, and the rule used to choose is stated.")
        st.dataframe(pd.DataFrame(conf), hide_index=True, use_container_width=True)
    st.subheader("Disbursement")
    d = run_sql(f"""SELECT end_of_period, sum(disbursed_usd) AS disbursed_usd FROM {CATALOG}.silver.disbursements
                    WHERE project_id = :p GROUP BY end_of_period ORDER BY end_of_period""", {"p": pid})
    if d:
        st.line_chart(pd.DataFrame(d).set_index("end_of_period"))
    else:
        st.caption("No disbursement series (e.g. Additional Financing recorded under its parent).")
    st.subheader("Contracts")
    c = run_sql(f"""SELECT c.contract_key, c.description, c.procurement_category, c.review_type, c.total_amount_usd,
                           c.signing_date, c.coverage_status, c.source_dataset, a.score AS amendment_indicator
                    FROM {CATALOG}.silver.contracts c
                    LEFT JOIN {CATALOG}.gold.amendment_scores a ON c.contract_key = a.contract_key
                    WHERE c.project_id = :p ORDER BY c.signing_date DESC""", {"p": pid})
    st.dataframe(pd.DataFrame(c), hide_index=True, use_container_width=True)


def ask_screen():
    st.header("Ask the evidence")
    st.caption("Answers use only the indexed documents (16 demo projects: reports, procurement notices, country "
               "CPFs). Write a project ID (e.g. P147864) or a contract reference to find it exactly.")
    q = st.text_input("Question", "What procurement lessons do ICRs report for rural road works?")
    as_of = st.date_input("Use documents published up to", value=date.today())
    if st.button("Answer", type="primary") and q:
        with st.spinner("Retrieving and checking…"):
            result = pl.run({"as_of": str(as_of)}, tools("ask"), task="ask", question=q)
        show_result(result, "ask", "ask")


def coverage_screen():
    st.header("Data coverage and caveats")
    _, path = backend()
    b, _ = backend()
    hybrid = {True: "Vector Search hybrid", False: "vector + keyword fallback", None: "not yet determined"}[b.hybrid_supported]
    st.markdown(f"- Retrieval path in use: **{path}** (keyword matching: {hybrid if b.vs_index else 'keyword fallback'}). "
                f"Retrieval gate threshold: **{min_score():.2f}** (calibrated on half of the evaluation set).")
    st.markdown("- Contract data: DS00005 (FY2020+, full), DS04691 (FY2017–19, partial; FY2017 likely incomplete), "
                "DS01004 (FY≤2016, prior-review only). Coverage is measured against published award notices.\n"
                "- CPIA covers mainly IDA countries: missing values stay missing, never zero.\n"
                "- Review type is 'unknown' where the source has none (all of DS01004).")
    cov = run_sql(f"SELECT * FROM {CATALOG}.gold.contract_coverage_fy ORDER BY fiscal_year")
    st.dataframe(pd.DataFrame(cov), hide_index=True, use_container_width=True)


PAGES = {"Plan a package": plan_screen, "Monitor a project": monitor_screen, "Ask the evidence": ask_screen,
         "Data coverage": coverage_screen}
choice = st.sidebar.radio("Screen", list(PAGES))
st.sidebar.caption("World Bank open data · Databricks · every claim cited")
_minfo = backend()[0].duration_model_info or {}     # loaded once per app process; status also goes to the app log
st.sidebar.caption(f"Duration model: version {_minfo['version']} loaded" if _minfo.get("version")
                   else f"Duration model not loaded: {_minfo.get('load_error', 'unknown reason')}")
if kpi_dashboard_url():
    st.sidebar.link_button("KPI dashboard", kpi_dashboard_url())
PAGES[choice]()
