"""Retrieval evaluation changes of 2026-10-22: routing, pure-vector gate, lenient recall."""
from src.evaluation import lenient as ln
from src.evaluation import metrics as ev
from src.rag import databricks_tools as dt
from src.rag import pipeline as pl
from src.rag import retrieval as rt


def test_outcome_questions_are_routed_to_completion_and_evaluation_reports():
    assert rt.route_doc_types("What overall outcome rating did the ICR give project P132833?") == rt.OUTCOME_DOC_TYPES
    assert rt.route_doc_types("How was the project rated by IEG?") == rt.OUTCOME_DOC_TYPES
    assert rt.route_doc_types("What lessons on packaging civil works?") is None


def test_hybrid_hits_get_pure_vector_scores_and_the_scope_top():
    hyb = [{"chunk_id": "a", "score": 0.99, "hybrid": True}, {"chunk_id": "b", "score": 0.98, "hybrid": True}]
    out = dt.attach_vector_scores(hyb, [{"chunk_id": "a", "score": 0.61}, {"chunk_id": "z", "score": 0.66}])
    assert out[0]["vector_score"] == 0.61 and out[1]["vector_score"] is None and out[0]["vector_top"] == 0.66
    merged = rt.hybrid_merge(out, [])
    assert merged[0]["vector_score"] == 0.61 and merged[0]["score"] == 0.99    # hybrid score kept for ranking only


def _tools(hits, min_score):
    return pl.Tools(embed=lambda t: [1.0], search=lambda v, f, k, text=None: [dict(h) for h in hits],
                    llm=lambda m: '{"claims": [], "insufficient_evidence": true}', verify_claims=False,
                    min_score=min_score)


def test_gate_uses_vector_similarity_not_the_hybrid_score():
    hits = [{"chunk_id": "a", "doc_id": "d", "score": 0.99, "hybrid": True, "vector_score": 0.40, "vector_top": 0.42,
             "text": "x", "doc_type": "Implementation Status and Results Report"}]
    r = pl.run({"as_of": "2026-01-01"}, _tools(hits, 0.5), task="ask", question="Lessons on bridges?")
    assert r.status == "refused_no_evidence" and r.top_score == 0.42


def test_routed_search_falls_back_when_restricted_scope_is_empty():
    seen = []
    def search(v, f, k, text=None):
        seen.append(f.get("doc_type"))
        return [] if f.get("doc_type") == rt.OUTCOME_DOC_TYPES and f.get("project_id") else \
            [{"chunk_id": "a", "doc_id": "d", "score": 0.7, "text": "x", "doc_type": "Implementation Status and Results Report"}]
    tools = pl.Tools(embed=lambda t: [1.0], search=search, llm=lambda m: '{"claims": [], "insufficient_evidence": true}',
                     verify_claims=False, min_score=0.0)
    r = pl.run({"as_of": "2026-01-01"}, tools, task="ask", question="What outcome rating did P132833 get?")
    assert r.route == rt.OUTCOME_DOC_TYPES and None in seen and r.retrieved == ["a"]


def test_lenient_recall_adds_passages_judged_to_contain_the_answer():
    rows = [{"qid": "q1", "retrieved": ["x", "g"], "gold": ["g"], "should_refuse": False, "status": "answered"},
            {"qid": "q2", "retrieved": ["y", "z"], "gold": ["w"], "should_refuse": False, "status": "answered"},
            {"qid": "r", "retrieved": ["y"], "gold": [], "should_refuse": True, "status": "refused_no_evidence"}]
    assert ln.to_judge(rows, {}) == [("q1", "x"), ("q2", "y"), ("q2", "z")]
    judged = {("q1", "x"): True, ("q2", "y"): False, ("q2", "z"): True}
    strict, lenient = ev.summarise(rows)["all"], ev.summarise(ln.lenient_rows(rows, judged))["all"]
    assert strict["recall@5"] == 0.5 and lenient["recall@5"] == 1.0 and lenient["mrr"] == (1 + 0.5) / 2


def test_judge_reply_parsing():
    assert ln.parse("YES") is True and ln.parse(" no.") is False and ln.parse("maybe") is None
    m = ln.build_messages("Q?", "ref", "cand")
    assert "YES or NO" in m[0]["content"] and "CANDIDATE" in m[1]["content"]
