"""Tests for the checkpoint (d) changes (2026-10-21): claim verifier, hybrid / keyword retrieval, notice chunks,
evaluation split and paraphrase check, re-bid modification notices."""
import json
from datetime import date

from src.chunking import notices as nc
from src.evaluation import questions as eq
from src.guardrails import claim_verifier as cv
from src.rag import keyword as kw
from src.rag import pipeline as pl
from src.rag import retrieval as rt
from src.risk import labels as lb

SOURCES = {"E1": "Splitting the works into 4 lots attracted 14 bidders.", "F1": "Contract C:1 took 245 days."}


# --- 1. claim-support verifier ---------------------------------------------------------------------------------------

def test_verifier_builds_one_batched_message_with_cited_text():
    msgs = cv.build_messages([{"text": "a", "citations": ["E1"]}, {"text": "b", "citations": ["F1"]}], SOURCES)
    assert len(msgs) == 2 and "CLAIM 1: a" in msgs[1]["content"] and "CLAIM 2: b" in msgs[1]["content"]
    assert "14 bidders" in msgs[1]["content"] and "245 days" in msgs[1]["content"]


def test_verifier_removes_unsupported_and_fails_closed():
    claims = [{"text": "Lots attracted 14 bidders.", "citations": ["E1"]},
              {"text": "Lots cut costs by half.", "citations": ["E1"]},
              {"text": "It took 245 days.", "citations": ["F1"]}]
    reply = json.dumps({"verdicts": [{"claim": 1, "supported": True, "reason": "stated"},
                                     {"claim": 2, "supported": False, "reason": "costs not mentioned"}]})
    calls = []
    kept, removed, rep = cv.verify(claims, SOURCES, lambda m: calls.append(m) or reply)
    assert len(calls) == 1                                       # one call for the whole answer
    assert [c["text"] for c in kept] == ["Lots attracted 14 bidders."]
    assert rep == {"verified": 1, "unsupported": 1, "no_verdict": 1}   # claim 3 had no verdict -> removed
    kept, removed, rep = cv.verify(claims, SOURCES, lambda m: "not json")
    assert kept == [] and rep["no_verdict"] == 3
    assert cv.parse_verdicts('{"verdicts": [{"claim": 9, "supported": true}, {"claim": 1, "supported": "yes"}]}', 2) == {}


def _tools(replies, hits, keyword=None):
    it = iter(replies)
    return pl.Tools(embed=lambda t: [0.1], search=lambda v, f, k, text=None: [dict(h) for h in hits],
                    llm=lambda m: next(it), keyword_search=keyword)


HIT = {"chunk_id": "c1", "doc_id": "d1", "doc_type": "Implementation Completion and Results Report",
       "doc_date": "2019-06-30", "project_id": "P147864", "section_path": "Lessons", "score": 0.8,
       "text": "Splitting the works into 4 lots attracted 14 bidders."}


def test_pipeline_runs_verifier_after_mechanical_checks_and_counts_removals():
    answer = json.dumps({"claims": [{"text": "Splitting into 4 lots attracted 14 bidders.", "citations": ["E1"]},
                                    {"text": "Splitting into lots always reduces delays.", "citations": ["E1"]},
                                    {"text": "It cost USD 5,000,000.", "citations": ["E1"]}]})
    verdict = json.dumps({"verdicts": [{"claim": 1, "supported": True}, {"claim": 2, "supported": False}]})
    r = pl.run({"as_of": "2024-01-01"}, _tools([answer, verdict], [HIT]), task="ask", question="lots?")
    assert [c["text"] for c in r.claims] == ["Splitting into 4 lots attracted 14 bidders."]
    rep = r.guardrail_report
    assert rep["claims"] == 3 and rep["kept"] == 1 and rep["removed"] == 2          # 1 mechanical + 1 verifier
    assert rep["verifier"] == {"verified": 1, "unsupported": 1, "no_verdict": 0}
    assert any(p.startswith("unsupported_numbers") for p in rep["problems"])
    assert any(p.startswith("verifier:unsupported") for p in rep["problems"])


# --- 2. hybrid / keyword retrieval -----------------------------------------------------------------------------------

def test_identifiers_find_codes_not_years_or_words():
    assert kw.identifiers("What happened to contract HAMP-CS-01 in P147864?") == ["P147864", "HAMPCS01"]
    assert kw.identifiers("Re-bid of GYTJ201B") == ["GYTJ201B"]
    assert kw.identifiers("lessons from FY2019 and COVID-19 on 2019-06-30 for rural roads") == []


def test_id_match_whole_code_ignoring_separators():
    assert kw.id_match("Package HAMP CS 01 was signed", ["HAMPCS01"]) == "HAMP CS 01"
    assert kw.id_match("Package HAMP-CS-012 was signed", ["HAMPCS01"]) is None


ROWS = [
    {"chunk_id": "a", "text": "All four rebid contracts, GYTJ201B and others, were signed late.", "project_id": "P1",
     "doc_type": "ISR", "doc_date_int": 20190101, "is_primary": True},
    {"chunk_id": "b", "text": "Rural road maintenance contracts used performance-based payments.", "project_id": "P1",
     "doc_type": "ISR", "doc_date_int": 20190101, "is_primary": True},
    {"chunk_id": "c", "text": "Bridge works on GYTJ201B were delayed.", "project_id": "P2",
     "doc_type": "ISR", "doc_date_int": 20250101, "is_primary": True},
]


def test_keyword_index_bm25_and_identifier_hits_respect_filters():
    idx = kw.KeywordIndex(ROWS)
    hits = idx.search("contract GYTJ201B", rt.filters_for(as_of=date(2020, 1, 1)), 5)
    assert hits[0]["chunk_id"] == "a" and hits[0]["id_match"] == "GYTJ201B" and hits[0]["kw_score"] == 1.0
    assert "c" not in [h["chunk_id"] for h in hits]                      # too late for the as-of date
    hits = idx.search("performance-based maintenance", {}, 5)
    assert hits[0]["chunk_id"] == "b" and hits[0]["id_match"] is None


def test_hybrid_merge_combines_scores_and_keeps_raw_similarity():
    m = {h["chunk_id"]: h for h in rt.hybrid_merge([{"chunk_id": "a", "score": 0.6}, {"chunk_id": "b", "score": 0.5}],
                                                   [{"chunk_id": "a", "kw_score": 1.0, "id_match": "X1"},
                                                    {"chunk_id": "z", "kw_score": 0.4}])}
    assert m["a"]["score"] == round(0.7 * 0.6 + 0.3, 4) and m["a"]["vector_score"] == 0.6 and m["a"]["id_match"] == "X1"
    assert m["z"]["vector_score"] is None and m["z"]["score"] == round(0.3 * 0.4, 4)
    vs = rt.hybrid_merge([{"chunk_id": "h", "score": 0.033, "hybrid": True}], [])
    assert vs[0]["score"] == 0.033                                       # Vector Search hybrid score kept as is


def test_identifier_match_passes_the_gate_even_with_low_similarity():
    low = dict(HIT, score=0.2, text="Contract GYTJ201B was re-bid after all bids exceeded the estimate.")
    answer = json.dumps({"claims": [], "insufficient_evidence": True})
    r = pl.run({"as_of": "2024-01-01"}, _tools([answer], [low]), task="ask", question="What happened to GYTJ201B?")
    assert r.identifier_hit and r.status == "insufficient_evidence"     # passed the gate, reached the model
    r = pl.run({"as_of": "2024-01-01"}, _tools([answer], [low]), task="ask", question="What happened to bridges?")
    assert r.status == "refused_no_evidence"


def test_pipeline_uses_keyword_tool_when_given():
    kw_hit = dict(HIT, chunk_id="k9", doc_id="d9", text="Contract GYTJ201B was re-bid.", kw_score=1.0,
                  id_match="GYTJ201B")
    tools = _tools([json.dumps({"claims": [], "insufficient_evidence": True})], [HIT],
                   keyword=lambda q, f, k: [dict(kw_hit)])
    r = pl.run({"as_of": "2024-01-01"}, tools, task="ask", question="GYTJ201B")
    assert r.retrieved[0] == "k9"                                        # identifier bonus ranks it first


def test_plan_task_searches_procurement_notices():
    seen = []
    tools = _tools([json.dumps({"claims": [], "insufficient_evidence": True})], [HIT])
    tools.search = lambda v, f, k, text=None: seen.append(f) or [dict(HIT)]
    pl.run({"as_of": "2024-01-01", "scope": "rural roads"}, tools, task="plan")
    assert any(f.get("doc_type") == [rt.NOTICE_DOC_TYPE] for f in seen)


class _FakeIndex:
    def __init__(self, hybrid_ok):
        self.hybrid_ok, self.calls = hybrid_ok, []

    def similarity_search(self, **kw):
        self.calls.append(kw.get("query_type", "ANN"))
        if kw.get("query_type") == "HYBRID" and not self.hybrid_ok:
            raise ValueError("hybrid not supported for this index")
        return {"manifest": {"columns": [{"name": "chunk_id"}, {"name": "text"}, {"name": "score"}]},
                "result": {"data_array": [["a", "GYTJ201B re-bid", 0.03]]}}


def test_backend_uses_vector_search_hybrid_or_falls_back_to_keywords():
    from src.rag.databricks_tools import DatabricksBackend
    ok = DatabricksBackend(sql=lambda q, p: [], workspace_client=None, vs_index=_FakeIndex(True))
    assert ok.search([0.1], {}, 5, "GYTJ201B")[0]["hybrid"] is True
    assert ok.keyword_search("GYTJ201B", {}, 5) == []                    # Vector Search does the keyword side
    no = DatabricksBackend(sql=lambda q, p: [dict(r) for r in ROWS], workspace_client=None, vs_index=_FakeIndex(False))
    assert no.search([0.1], {}, 5, "GYTJ201B")[0]["hybrid"] is False     # ANN after the refused HYBRID call
    assert no.search([0.1], {}, 5, "x") and no.vs_index.calls == ["HYBRID", "ANN", "ANN"]   # not retried
    assert no.keyword_search("GYTJ201B", {}, 5)[0]["id_match"] == "GYTJ201B"


# --- 3. procurement notices ------------------------------------------------------------------------------------------

def test_notice_chunks_clean_html_and_carry_provenance():
    text = ("<p>REQUEST FOR EXPRESSIONS OF INTEREST</p><p>Qualification: 10 years&#39; experience in road design, "
            "average annual turnover of USD 2 million.</p>" + "<p>" + "Scope of services. " * 400 + "</p>")
    n = {"notice_id": "OP00012345", "notice_type": "Request for Expression of Interest", "project_id": "P147864",
         "notice_date": date(2018, 3, 1), "bid_reference_no": "IN-RD-CS-07", "bid_description": "Design consultancy",
         "notice_text": text}
    ch = nc.notice_chunks(n)
    assert len(ch) >= 2 and all(c["doc_type"] == "Procurement Notice" for c in ch)
    assert "10 years' experience" in ch[0]["text"] and "<p>" not in ch[0]["text"]
    assert ch[0]["chunk_id"] == "notice-OP00012345-0000" and ch[0]["section_path"].endswith("IN-RD-CS-07")
    assert all(len(c["text"]) <= nc.TARGET_CHARS for c in ch)
    assert nc.notice_chunks(dict(n, notice_type="Contract Award")) == []
    assert nc.notice_chunks(dict(n, notice_text="short")) == []


# --- 4/5. evaluation split and paraphrase check ----------------------------------------------------------------------

def test_split_half_is_deterministic_stratified_and_disjoint():
    qs = [{"qid": f"{k}:{i}", "kind": k} for k in ("rating", "lesson", "refusal") for i in range(10)]
    a1, b1 = eq.split_half(qs)
    a2, b2 = eq.split_half(list(reversed(qs)))
    assert {q["qid"] for q in a1} == {q["qid"] for q in a2}
    assert not {q["qid"] for q in a1} & {q["qid"] for q in b1} and len(a1) + len(b1) == 30
    assert all(sum(q["kind"] == k for q in a1) == 5 for k in ("rating", "lesson", "refusal"))


def test_calibrate_threshold_on_calibration_rows():
    rows = [{"top_score": s, "should_refuse": False} for s in (0.6, 0.65, 0.7, 0.8, 0.9, 0.62, 0.75, 0.85, 0.66, 0.71)]
    rows += [{"top_score": 0.4, "should_refuse": True},
             {"top_score": 0.2, "should_refuse": False, "identifier_hit": True}]
    th, rep = eq.calibrate_threshold(rows)
    assert rep["answerable_passing"] >= 0.9 and th >= 0.6 and rep["refusals_blocked_by_gate"] == 1.0


def test_paraphrase_prompt_and_overlap():
    msgs = eq.question_messages("tender_design", "Bidders need 10 years of experience.")
    assert "do NOT reuse the passage's key phrases" in msgs[1]["content"] and "qualification" in msgs[1]["content"]
    passage = "Contractors lacked capacity, so packages were split into smaller lots to attract local firms."
    assert eq.lexical_overlap("Why were packages split into smaller lots to attract local firms?", passage) > 0.8
    assert eq.lexical_overlap("How can tender size be adapted to domestic market capacity?", passage) < 0.5
    replies = iter(["Why were packages split into smaller lots to attract local firms?",
                    "How can tender size be adapted to domestic market capacity?"])
    q, ov = eq.generate_question(lambda m: next(replies), "lesson", passage)
    assert q.startswith("How can") and ov < eq.MAX_OVERLAP                # regenerated once, kept the paraphrase


# --- 6. re-bid label ignores modification notices --------------------------------------------------------------------

def _n(d, desc="", status="Published", text=""):
    return {"notice_type": "Invitation for Bids", "notice_date": d, "notice_status": status, "bid_description": desc,
            "notice_text": text}


def test_corrigendum_amendment_extension_do_not_count_as_second_ifb():
    first = _n(date(2020, 1, 1), "Construction of rural roads, Package 4")
    for later in (_n(date(2020, 3, 1), "Corrigendum: Construction of rural roads, Package 4"),
                  _n(date(2020, 3, 1), "Construction of rural roads, Package 4", text="Amendment No. 1 to the IFB"),
                  _n(date(2020, 3, 1), "Extension of bid submission deadline - Package 4"),
                  _n(date(2020, 3, 1), "Package 4", status="Revised"),
                  _n(date(2020, 3, 1), "Additif n°1 - Travaux de pistes rurales")):
        assert lb.tender_label([first, later])["rebid"] is False, later
    # a genuine second invitation still counts, and works wording like "extension of the road" is not a modification
    assert lb.tender_label([first, _n(date(2020, 4, 1), "Extension of the road to Kiban, Package 4")])["rebid"]
