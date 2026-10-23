"""Question understanding for Ask (src/rag/query.py) and its use in pipeline.run: instructions are not search terms,
project names become IDs, rating questions get the rating timeline as facts."""
from src.rag import pipeline as pl
from src.rag import query as qy

Q = ("How was implementation progress rated over time for the Yunnan highway project, and did the final rating differ "
     "from earlier progress reports?")


def test_instructions_are_removed_from_the_search_query():
    plain, s0 = qy.split_instructions(Q)
    for variant in ("Tell me in detail: " + Q, Q[:-1] + " in detail?", "Please explain in detail. " + Q,
                    "Can you summarise " + Q[0].lower() + Q[1:]):
        q, style = qy.split_instructions(variant)
        assert q == plain, (variant, q)
        assert style
    assert s0 == ""
    assert qy.split_instructions("Explain why packages were re-bid in Bihar")[0] == "Why packages were re-bid in Bihar"
    assert qy.split_instructions("explain")[0] == "explain"            # nothing but an instruction: kept as is


PROJECTS = [
    {"project_id": "P132621", "project_name": "Yunnan Highway Asset Management Project", "in_slice": True},
    {"project_id": "P101525", "project_name": "CN Yunnan Honghe Prefecture Diannan Center Urban Transport", "in_slice": False},
    {"project_id": "P147864", "project_name": "Sichuan Rural Roads Project", "in_slice": True},
    {"project_id": "P144726", "project_name": "Gujarat Rural Roads Project", "in_slice": False},
    {"project_id": "P157054", "project_name": "Madhya Pradesh Rural Connectivity Project", "in_slice": False},
]


def test_project_names_resolve_to_ids():
    assert qy.resolve_project_names(Q, PROJECTS) == ["P132621"]           # "Yunnan" + "highway" beat "Yunnan" alone
    assert qy.resolve_project_names("Lessons from Madhya Pradesh rural roads", PROJECTS) == ["P157054"]
    assert qy.resolve_project_names("What lessons do rural road projects report?", PROJECTS) == []   # generic words only
    assert qy.resolve_project_names("", PROJECTS) == []


RATINGS = [
    {"project_id": "P132621", "dimension": "implementation", "value": "Moderately Satisfactory", "score": 4,
     "doc_type": "Implementation Status and Results Report", "doc_date": "2018-12-30", "chunk_id": "30732569-0001"},
    {"project_id": "P132621", "dimension": "implementation", "value": "Satisfactory", "score": 5,
     "doc_type": "Implementation Status and Results Report", "doc_date": "2021-01-19", "chunk_id": "32758109-0001"},
    {"project_id": "P132621", "dimension": "outcome", "value": "Moderately Satisfactory", "score": 4,
     "doc_type": "Implementation Status and Results Report", "doc_date": "2020-06-24", "chunk_id": "32169792-0001"},
    {"project_id": "P132621", "dimension": "outcome", "value": "Highly Satisfactory", "score": 6,
     "doc_type": "Implementation Completion Report Review", "doc_date": "2023-05-05", "chunk_id": "34057527-0021"},
]


def test_rating_facts_timeline_rule_and_scale_note():
    facts = qy.rating_facts(RATINGS)
    texts = [f["text"] for f in facts]
    assert sum(f["kind"] == "rating" for f in facts) == 4
    assert any("final development outcome" in t and "Highly Satisfactory" in t and "IEG review > ICR" in t for t in texts)
    assert all("source passage" in t for t in texts if "rated" in t)
    assert "efficacy" in texts[-1]
    assert qy.is_rating_question(Q) and qy.is_trend_question(Q)
    assert not qy.is_trend_question("What was the final outcome rating of P132621?")


def _hit(cid, pid="P132621", score=0.8):
    return {"chunk_id": cid, "doc_id": cid.split("-")[0], "project_id": pid, "doc_type": "Implementation Status and Results Report",
            "score": score, "vector_score": score, "text": "Implementation progress rated Satisfactory", "section_path": ""}


def test_both_phrasings_search_the_same_query_and_get_rating_facts():
    seen, prompts = [], []

    def search(vec, filters, k, text=None):
        seen.append((text, tuple(filters.get("project_id") or ()), tuple(filters.get("doc_type") or ())))
        return [_hit("32758109-0001")]

    def llm(messages):
        prompts.append(messages[-1]["content"])
        return '{"claims": [], "insufficient_evidence": true}'

    tools = pl.Tools(embed=lambda t: [1.0], search=search, llm=llm, verify_claims=False, min_score=0.5,
                     resolve_projects=lambda q: qy.resolve_project_names(q, PROJECTS), rating_rows=lambda pids: RATINGS)
    r1 = pl.run({"as_of": "2026-10-23"}, tools, task="ask", question=Q)
    n1 = len(seen)
    r2 = pl.run({"as_of": "2026-10-23"}, tools, task="ask", question="Tell me in detail: " + Q)
    assert seen[:n1] == seen[n1:]                                        # identical searches
    assert r1.retrieved == r2.retrieved and r1.resolved_projects == ["P132621"]
    assert all(pids == ("P132621",) for _, pids, _ in seen if pids)      # project filter from the name
    assert all(not dts for _, pids, dts in seen if pids)                 # trend question: ISRs not routed away
    assert "rated Highly Satisfactory" in prompts[0] and "STRUCTURED FACTS" in prompts[0]
    assert "Answer style requested by the user" in prompts[1] and "Answer style" not in prompts[0]


def test_robustness_helpers():
    from src.evaluation import robustness as rb
    a, b = rb.variants("What lessons?")
    assert a == "What lessons?" and b.startswith("Tell me in detail")
    assert rb.overlap(["x", "y", "z"], ["x", "y", "z"]) == 1.0 and rb.overlap(["x", "y"], ["y", "q"]) == 0.333
    assert rb.parse_judge("YES.") is True and rb.parse_judge("no") is False and rb.parse_judge("maybe") is None
    s = rb.summarise([{"qid": "a", "overlap": 1.0, "same_status": True, "agree": True},
                      {"qid": "b", "overlap": 0.5, "same_status": False, "agree": None}])
    assert s["identical_top5"] == 1 and s["mean_top5_overlap"] == 0.75 and s["answers_judged"] == 1


def test_guardrail_sources_include_passage_date():
    from src.guardrails import citation_check as gc
    from src.rag import prompt as pr
    _, sources, _ = pr.format_evidence([{"chunk_id": "c1", "doc_type": "Implementation Status and Results Report",
                                         "doc_date": "2016-06-20", "project_id": "P132621",
                                         "text": "Implementation progress: Moderately Satisfactory", "section_path": ""}])
    out = gc.check_answer({"claims": [{"text": "The June 2016 ISR rated implementation progress Moderately Satisfactory.",
                                       "citations": ["E1"]}], "insufficient_evidence": False}, sources)
    assert len(out["claims"]) == 1 and not out["removed"]
