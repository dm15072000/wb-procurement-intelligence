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
from src.rag import multilingual as ml
from src.rag import query as qy
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
    # French retrieval (2026-10-23): keyword search ranked over all passages, using the English translation of the
    # translated (French) passages, hits flagged "translated"; and a question -> French question translator (1 LLM
    # call), used only when the question concerns a French-speaking country. Both optional.
    translated_keyword_search: Optional[Callable[[str, dict, int], list]] = None
    translate_query: Optional[Callable[[str], Optional[str]]] = None
    # Ask questions (2026-10-23): project names -> IDs (question -> [project_id]) and the extracted rating timeline as
    # structured facts ([project_id] -> rows of gold.ratings). Both optional.
    resolve_projects: Optional[Callable[[str], list]] = None
    rating_rows: Optional[Callable[[list], list]] = None
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
    french_query: Optional[str] = None                   # French version of the question, when one was searched
    search_query: Optional[str] = None                   # the question without answer-style instructions
    style: str = ""                                      # instructions removed from the search ("in detail", ...)
    resolved_projects: list = field(default_factory=list)  # project IDs found from project NAMES in the question
    identifier_hit: bool = False                         # a queried project ID / reference was found verbatim
    timings: dict = field(default_factory=dict)


def package_text(pkg):
    """One readable line describing the package; also the retrieval query."""
    parts = [pkg.get("scope") or "", f"Category: {pkg.get('category')}" if pkg.get("category") else "",
             f"Method: {pkg.get('method')}" if pkg.get("method") else "",
             f"Country: {pkg.get('country_name') or pkg.get('country_iso3')}" if pkg.get("country_iso3") else "",
             f"Estimated value USD {pkg['amount_usd']:,.0f}" if pkg.get("amount_usd") else ""]
    return ". ".join(p for p in parts if p)


def _scope_search(tools, vec, query, filters, english=True):
    """One scope = vector (or Vector Search hybrid) hits, fused with keyword hits when a keyword tool is given. For an
    English query, translated (French) passages found by English keyword matching are added at their keyword rank."""
    vh = tools.search(vec, filters, tools.k_search, query)
    kh = tools.keyword_search(query, filters, tools.k_search) if tools.keyword_search else []
    hits = rt.hybrid_merge(vh, kh)
    if english and tools.translated_keyword_search:
        hits = ml.rank_aligned(hits, tools.translated_keyword_search(query, filters, tools.k_search))
    return hits


def _scopes(as_of, project_ids, task, doc_types):
    """(name, filters, fallback filters if the routed search is empty) for every search scope."""
    out = []
    if project_ids:
        out.append(("project_scope", rt.filters_for(as_of, project_ids=project_ids, doc_types=doc_types),
                    rt.filters_for(as_of, project_ids=project_ids) if doc_types else None))
    out.append(("lessons_scope", rt.filters_for(as_of, doc_types=rt.LESSON_DOC_TYPES), None))
    if task == "plan":   # tender design (L2): past REOI / IFB text with scope and qualification criteria
        out.append(("notice_scope", rt.filters_for(as_of, doc_types=[rt.NOTICE_DOC_TYPE]), None))
    if not project_ids:
        out.append(("open_scope", rt.filters_for(as_of, doc_types=doc_types),
                    rt.filters_for(as_of) if doc_types else None))
    return out


def retrieve(query, pkg, tools, project_ids=(), contract_keys=(), task="ask", doc_types=None, info=None):
    """Steps 3-6: project-scoped, lessons-scoped and (for planning) notice-scoped hybrid search, DESCRIBES links,
    merge, identifier marking, rerank. doc_types (question routing) restricts the project and open scopes; if the
    restricted search finds nothing, it is repeated without the restriction. For a French-speaking country the same
    scopes are searched again with the question in French (info["french_query"] records it)."""
    vec = tools.embed(query)
    scopes = _scopes(pkg.get("as_of"), project_ids, task, doc_types)
    lists = []
    for name, filters, fallback in scopes:
        hits = _scope_search(tools, vec, query, filters)
        if fallback and not hits:
            hits = _scope_search(tools, vec, query, fallback)
        lists.append((name, hits))
    if tools.translate_query and ml.french_context(query, pkg):
        fq = tools.translate_query(query)
        if fq:
            if info is not None:
                info["french_query"] = fq
            fvec = tools.embed(fq)
            for name, filters, fallback in scopes:
                hits = _scope_search(tools, fvec, fq, filters, english=False)
                if fallback and not hits:
                    hits = _scope_search(tools, fvec, fq, fallback, english=False)
                lists.append((name + "_fr", hits))
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
    contract_keys = [f["contract_key"] for f in facts if f.get("contract_key")]
    # Query understanding (deterministic). Answer-style instructions ("tell me in detail") are not search terms: they
    # are removed from the search query and passed to the answer step. Project IDs written in the question, and
    # project NAMES matched to IDs, scope the search to those projects.
    search_q, style = qy.split_instructions(question) if task == "ask" else (None, "")
    named = re.findall(r"\bP\d{6}\b", question or "") if task == "ask" else []
    resolved = [p for p in (tools.resolve_projects(search_q) if task == "ask" and tools.resolve_projects else [])
                if p not in named]
    related = list(tools.related_projects(pkg, facts)) if task != "ask" else []
    projects = list(dict.fromkeys(([pkg["project_id"]] if pkg.get("project_id") else []) + named + resolved + related))
    # Rating questions about known projects: the extracted rating timeline, the final-outcome rule and conflict flags
    # become structured facts, so the answer can show the timeline with sources.
    if task == "ask" and projects and tools.rating_rows and qy.is_rating_question(search_q):
        facts = qy.rating_facts(tools.rating_rows(projects))
    timings["sql_and_models_s"] = round(time.time() - t0, 2)
    query = search_q if task == "ask" else package_text(pkg)
    # Final-outcome questions are routed to ICR / IEG; questions about change over time keep the ISRs.
    route = (None if qy.is_trend_question(search_q) else rt.route_doc_types(search_q)) if task == "ask" else None
    t1 = time.time()
    info = {}
    hits = retrieve(query, pkg, tools, projects, contract_keys, task, doc_types=route, info=info)
    timings["retrieval_s"] = round(time.time() - t1, 2)
    # Gate: the best PURE vector similarity must clear the threshold, unless a queried identifier (project ID,
    # contract reference) was found verbatim: an exact code match is evidence even when embeddings rate it low.
    # Keyword-only hits and combined hybrid scores never count (they were ~1.0 for every question, 2026-10-22).
    sims = [s for h in hits if "describes_link" not in (h.get("found_by") or [])
            for s in (h.get("vector_score"), h.get("vector_top")) if s is not None]
    result = Result(status="answered", retrieved=[h["chunk_id"] for h in hits], timings=timings,
                    top_score=max(sims, default=0.0), route=route, french_query=info.get("french_query"),
                    search_query=search_q, style=style, resolved_projects=resolved,
                    identifier_hit=any(h.get("id_match") for h in hits))
    # Rating facts from extracted ratings (each with its source passage) also count as evidence for the gate.
    has_rating_facts = any(f.get("kind") == "rating" for f in facts)
    if not (result.top_score >= tools.min_score or result.identifier_hit or has_rating_facts):
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
    request = (search_q + (f"\n(Answer style requested by the user: {style}.)" if style else "")) if task == "ask" \
        else package_text(pkg)
    messages = pr.build_messages(task, request, ev_block, f_block)
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
                   "identifier_hit": result.identifier_hit, "timings": result.timings,
                   "search_query": result.search_query, "style": result.style,
                   "resolved_projects": result.resolved_projects, "route": result.route})
    return result
