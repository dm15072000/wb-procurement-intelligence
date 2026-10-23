"""The agentic retrieval flow as a fixed sequence of tools (docs/03_model_rag_design.md §3b). Unit-tested with fakes.

Plain-language summary: when a user starts a procurement package, the system runs the same steps every time:
find similar past contracts (SQL) → get model predictions → find related projects → search their documents,
sector-wide lessons and past procurement notices (hybrid: vector + keyword) → merge and rerank → refuse if nothing
relevant was found → ask the LLM for a cited brief → check every claim mechanically → ask a verifier LLM, in one
batched call, whether each remaining claim is really supported by its sources → drop the unsupported ones.
A fixed sequence is easier to test, explain and audit than an open-ended agent, and it is enough for this task.

Every data access is a function passed in through `Tools`, so this exact code runs in the evaluation notebook, in
the app, and in the unit tests (with fake tools).
"""
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.guardrails import citation_check as gc
from src.guardrails import claim_verifier as cv
from src.rag import keyword as kw
from src.rag import prompt as pr
from src.rag import retrieval as rt


@dataclass
class Tools:
    embed: Callable[[str], list]                         # text -> vector
    search: Callable[..., list]                          # (vector, filters, k, text) -> hits with 'score'
    llm: Callable[[list], str]                           # chat messages -> raw text
    similar_contracts: Callable[[dict], list] = lambda pkg: []      # -> facts [{"text", "contract_key", ...}]
    predictions: Callable[[dict], list] = lambda pkg: []            # -> facts from models
    related_projects: Callable[[dict, list], list] = lambda pkg, facts: []   # -> project ids
    describes_hits: Callable[[list], list] = lambda contract_keys: []        # -> hits from DESCRIBES links
    # Keyword side of hybrid retrieval (checkpoint d change 2): (query, filters, k) -> hits with kw_score / id_match.
    # Leave None when `search` already runs Vector Search in HYBRID mode.
    keyword_search: Optional[Callable[[str, dict, int], list]] = None
    verify_claims: bool = True                           # batched claim-support verifier (change 1)
    verifier_llm: Optional[Callable[[list], str]] = None  # defaults to llm
    min_score: float = 0.55                              # retrieval gate; calibrated in notebook 61
    k_search: int = 12
    k_final: int = 8
    log: Optional[Callable[[dict], None]] = None


@dataclass
class Result:
    status: str                                          # answered | refused_no_evidence | refused_unparseable | insufficient_evidence
    claims: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)         # E# -> hit (for the sources panel)
    facts: dict = field(default_factory=dict)            # F# -> fact
    guardrail_report: dict = field(default_factory=dict)
    injection_flags: dict = field(default_factory=dict)  # chunk_id -> phrases; these chunks are quarantined
    retrieved: list = field(default_factory=list)        # ranked chunk_ids (for retrieval metrics)
    top_score: float = 0.0                               # best PURE vector similarity (calibrates the retrieval gate)
    route: Optional[list] = None                         # document types the question was routed to, if any
    identifier_hit: bool = False                         # a queried project ID / reference was found verbatim
    timings: dict = field(default_factory=dict)


def package_text(pkg):
    """One readable line describing the package; also the retrieval query."""
    parts = [pkg.get("scope") or "", f"Category: {pkg.get('category')}" if pkg.get("category") else "",
             f"Method: {pkg.get('method')}" if pkg.get("method") else "",
             f"Country: {pkg.get('country_name') or pkg.get('country_iso3')}" if pkg.get("country_iso3") else "",
             f"Estimated value USD {pkg['amount_usd']:,.0f}" if pkg.get("amount_usd") else ""]
    return ". ".join(p for p in parts if p)


def _scope_search(tools, vec, query, filters):
    """One scope = vector (or Vector Search hybrid) hits, fused with keyword hits when a keyword tool is given."""
    vh = tools.search(vec, filters, tools.k_search, query)
    kh = tools.keyword_search(query, filters, tools.k_search) if tools.keyword_search else []
    return rt.hybrid_merge(vh, kh)


def retrieve(query, pkg, tools, project_ids=(), contract_keys=(), task="ask", doc_types=None):
    """Steps 3-6: project-scoped, lessons-scoped and (for planning) notice-scoped hybrid search, DESCRIBES links,
    merge, identifier marking, rerank. doc_types (question routing) restricts the project and open scopes; if the
    restricted search finds nothing, it is repeated without the restriction."""
    vec = tools.embed(query)
    as_of = pkg.get("as_of")
    lists = []
    if project_ids:
        hits = _scope_search(tools, vec, query, rt.filters_for(as_of, project_ids=project_ids, doc_types=doc_types))
        if doc_types and not hits:
            hits = _scope_search(tools, vec, query, rt.filters_for(as_of, project_ids=project_ids))
        lists.append(("project_scope", hits))
    lists.append(("lessons_scope",
                  _scope_search(tools, vec, query, rt.filters_for(as_of, doc_types=rt.LESSON_DOC_TYPES))))
    if task == "plan":   # tender design (L2): past REOI / IFB text with scope and qualification criteria
        lists.append(("notice_scope",
                      _scope_search(tools, vec, query, rt.filters_for(as_of, doc_types=[rt.NOTICE_DOC_TYPE]))))
    if not project_ids:
        hits = _scope_search(tools, vec, query, rt.filters_for(as_of, doc_types=doc_types))
        if doc_types and not hits:
            hits = _scope_search(tools, vec, query, rt.filters_for(as_of))
        lists.append(("open_scope", hits))
    if contract_keys:
        lists.append(("describes_link", tools.describes_hits(list(contract_keys))))
    merged = rt.merge(*lists)
    ids = kw.identifiers(query)
    for h in merged:   # mark passages containing a queried identifier, whichever search found them
        h["id_match"] = h.get("id_match") or (kw.id_match(h.get("text"), ids) if ids else None)
    return rt.rerank(merged, k=tools.k_final)


def run(pkg, tools, task="plan", question=None):
    """Run the whole flow. pkg: country_iso3, project_id, category, method, amount_usd, scope, as_of.
    task "ask" uses `question` as both the request and the retrieval query."""
    t0 = time.time()
    timings = {}
    facts = [] if task == "ask" else tools.similar_contracts(pkg) + tools.predictions(pkg)
    timings["sql_and_models_s"] = round(time.time() - t0, 2)
    contract_keys = [f["contract_key"] for f in facts if f.get("contract_key")]
    # Query understanding (deterministic): project IDs written in a question scope the search to those projects.
    named = re.findall(r"\bP\d{6}\b", question or "") if task == "ask" else []
    related = list(tools.related_projects(pkg, facts)) if task != "ask" else []
    projects = list(dict.fromkeys(([pkg["project_id"]] if pkg.get("project_id") else []) + named + related))
    query = question if task == "ask" else package_text(pkg)
    route = rt.route_doc_types(question) if task == "ask" else None
    t1 = time.time()
    hits = retrieve(query, pkg, tools, projects, contract_keys, task, doc_types=route)
    timings["retrieval_s"] = round(time.time() - t1, 2)
    # Gate: the best PURE vector similarity must clear the threshold, unless a queried identifier (project ID,
    # contract reference) was found verbatim: an exact code match is evidence even when embeddings rate it low.
    # Keyword-only hits and combined hybrid scores never count (they were ~1.0 for every question, 2026-10-22).
    sims = [s for h in hits if "describes_link" not in (h.get("found_by") or [])
            for s in (h.get("vector_score"), h.get("vector_top")) if s is not None]
    result = Result(status="answered", retrieved=[h["chunk_id"] for h in hits], timings=timings,
                    top_score=max(sims, default=0.0), route=route,
                    identifier_hit=any(h.get("id_match") for h in hits))
    if not (result.top_score >= tools.min_score or result.identifier_hit):
        result.status = "refused_no_evidence"
        return _log(tools, pkg, result)
    # Quarantine: a passage containing instruction-like text ("ignore previous instructions") is NOT shown to the
    # model. Flagging alone is not enough: a planted passage could otherwise "support" the claim it asks for.
    result.injection_flags = {h["chunk_id"]: fl for h in hits if (fl := gc.injection_flags(h.get("text")))}
    hits = [h for h in hits if h["chunk_id"] not in result.injection_flags]
    if not hits:
        result.status = "refused_no_evidence"
        return _log(tools, pkg, result)
    ev_block, ev_sources, ev_meta = pr.format_evidence(hits)
    f_block, f_sources, f_meta = pr.format_facts(facts)
    result.evidence, result.facts = ev_meta, f_meta
    messages = pr.build_messages(task, question or package_text(pkg), ev_block, f_block)
    t2 = time.time()
    answer = pr.parse_answer(tools.llm(messages))
    timings["llm_s"] = round(time.time() - t2, 2)
    if answer is None:
        result.status = "refused_unparseable"
        return _log(tools, pkg, result)
    sources = {**ev_sources, **f_sources}
    checked = gc.check_answer(answer, sources)                     # mechanical checks first (cheap)
    kept, removed, report = checked["claims"], list(checked["removed"]), dict(checked["report"])
    report["verifier"] = None
    if tools.verify_claims and kept:                               # then ONE batched LLM support check
        t3 = time.time()
        kept, v_removed, v_report = cv.verify(kept, sources, tools.verifier_llm or tools.llm)
        timings["verifier_s"] = round(time.time() - t3, 2)
        removed += v_removed
        report.update(kept=len(kept), removed=len(removed), verifier=v_report,
                      problems=report["problems"] + [p for r in v_removed for p in r["problems"]])
    result.claims, result.removed, result.guardrail_report = kept, removed, report
    if answer["insufficient_evidence"] or not result.claims:
        result.status = "insufficient_evidence"
    timings["total_s"] = round(time.time() - t0, 2)
    return _log(tools, pkg, result)


def _log(tools, pkg, result):
    if tools.log:
        tools.log({"package": pkg, "status": result.status, "retrieved": result.retrieved,
                   "guardrail_report": result.guardrail_report, "injection_flags": result.injection_flags,
                   "identifier_hit": result.identifier_hit, "timings": result.timings})
    return result
