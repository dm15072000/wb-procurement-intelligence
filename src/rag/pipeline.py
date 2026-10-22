"""The agentic retrieval flow as a fixed sequence of tools (docs/03_model_rag_design.md §3b). Unit-tested with fakes.

Plain-language summary: when a user starts a procurement package, the system runs the same steps every time:
find similar past contracts (SQL) → get model predictions → find related projects → search their documents and
sector-wide lessons (vector search) → merge and rerank → refuse if nothing relevant was found → ask the LLM for a
cited brief → check every claim and drop the unsupported ones. A fixed sequence is easier to test, explain and audit
than an open-ended agent, and it is enough for this task.

Every data access is a function passed in through `Tools`, so this exact code runs in the evaluation notebook, in
the app, and in the unit tests (with fake tools).
"""
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.guardrails import citation_check as gc
from src.rag import prompt as pr
from src.rag import retrieval as rt


@dataclass
class Tools:
    embed: Callable[[str], list]                         # text -> vector
    search: Callable[[list, dict, int], list]            # (vector, filters, k) -> hits with 'score'
    llm: Callable[[list], str]                           # chat messages -> raw text
    similar_contracts: Callable[[dict], list] = lambda pkg: []      # -> facts [{"text", "contract_key", ...}]
    predictions: Callable[[dict], list] = lambda pkg: []            # -> facts from models
    related_projects: Callable[[dict, list], list] = lambda pkg, facts: []   # -> project ids
    describes_hits: Callable[[list], list] = lambda contract_keys: []        # -> hits from DESCRIBES links
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
    top_score: float = 0.0                               # best similarity (to calibrate the retrieval gate)
    timings: dict = field(default_factory=dict)


def package_text(pkg):
    """One readable line describing the package; also the retrieval query."""
    parts = [pkg.get("scope") or "", f"Category: {pkg.get('category')}" if pkg.get("category") else "",
             f"Method: {pkg.get('method')}" if pkg.get("method") else "",
             f"Country: {pkg.get('country_name') or pkg.get('country_iso3')}" if pkg.get("country_iso3") else "",
             f"Estimated value USD {pkg['amount_usd']:,.0f}" if pkg.get("amount_usd") else ""]
    return ". ".join(p for p in parts if p)


def retrieve(query, pkg, tools, project_ids=(), contract_keys=()):
    """Steps 3-6: project-scoped search, lessons-scoped search, DESCRIBES links, merge, rerank."""
    vec = tools.embed(query)
    as_of = pkg.get("as_of")
    lists = []
    if project_ids:
        lists.append(("project_scope", tools.search(vec, rt.filters_for(as_of, project_ids=project_ids),
                                                    tools.k_search)))
    lists.append(("lessons_scope", tools.search(vec, rt.filters_for(as_of, doc_types=rt.LESSON_DOC_TYPES),
                                                tools.k_search)))
    if not project_ids:
        lists.append(("open_scope", tools.search(vec, rt.filters_for(as_of), tools.k_search)))
    if contract_keys:
        lists.append(("describes_link", tools.describes_hits(list(contract_keys))))
    return rt.rerank(rt.merge(*lists), k=tools.k_final)


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
    t1 = time.time()
    hits = retrieve(query, pkg, tools, projects, contract_keys)
    timings["retrieval_s"] = round(time.time() - t1, 2)
    similarity_hits = [h for h in hits if "describes_link" not in (h.get("found_by") or [])] or hits
    result = Result(status="answered", retrieved=[h["chunk_id"] for h in hits], timings=timings,
                    top_score=max((h.get("score") or 0.0 for h in similarity_hits), default=0.0))
    if not gc.retrieval_gate(similarity_hits, tools.min_score):
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
    checked = gc.check_answer(answer, {**ev_sources, **f_sources})
    result.claims, result.removed, result.guardrail_report = checked["claims"], checked["removed"], checked["report"]
    if answer["insufficient_evidence"] or not result.claims:
        result.status = "insufficient_evidence"
    timings["total_s"] = round(time.time() - t0, 2)
    return _log(tools, pkg, result)


def _log(tools, pkg, result):
    if tools.log:
        tools.log({"package": pkg, "status": result.status, "retrieved": result.retrieved,
                   "guardrail_report": result.guardrail_report, "injection_flags": result.injection_flags,
                   "timings": result.timings})
    return result
