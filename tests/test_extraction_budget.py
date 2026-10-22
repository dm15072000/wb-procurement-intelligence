"""Notebook 55 call budget: full vs limited scope, latest 3 ISRs per project, resumable skip of done chunks."""
from src.extraction import llm_extract as lx

ICR, REV, ISR = "Implementation Completion and Results Report", "Implementation Completion Report Review", lx.ISR_TYPE
PPAR, PP = "Project Performance Assessment Report", "Project Paper"


def _c(cid, doc, dtype, date, project="P1", text="The contract was amended to extend the completion date.",
       section="Implementation", primary=True):
    return {"chunk_id": cid, "doc_id": doc, "doc_type": dtype, "doc_date": date, "project_id": project,
            "text": text, "section_path": section, "is_primary": primary, "text_hash": "h" + cid}


CHUNKS = ([_c(f"isr{i}", f"isrdoc{i}", ISR, f"20{10 + i}-01-01") for i in range(6)]          # 6 ISRs, P1
          + [_c("icr1", "icrdoc", ICR, "2020-06-30"),
             _c("icr2", "icrdoc", ICR, "2020-06-30", section="V. Lessons Learned"),     # lessons + events
             _c("rev1", "revdoc", REV, "2021-01-01"),
             _c("ppar1", "ppardoc", PPAR, "2024-01-01"),
             _c("pp1", "ppdoc", PP, "2017-01-01"),
             _c("dup", "icrdoc", ICR, "2020-06-30", primary=False),                       # duplicate copy: never
             _c("plain", "icrdoc", ICR, "2020-06-30", text="Rural roads connect villages."),  # no event keyword
             _c("p2isr", "p2isrdoc", ISR, "2019-01-01", project="P2")])


def test_full_scope_under_budget():
    todo, info = lx.select_requests(CHUNKS, budget=100)
    assert info["mode"] == "full" and info["full_scope_calls"] == 13      # 12 event chunks + 1 lessons call
    assert info["full_scope_by_kind"] == {"lessons": 1, "events": 12}
    assert {c["chunk_id"] for c, _ in todo} >= {"ppar1", "pp1", "isr0"} and "dup" not in {c["chunk_id"] for c, _ in todo}


def test_over_budget_limits_to_icr_review_and_latest_3_isrs():
    todo, info = lx.select_requests(CHUNKS, budget=5)
    assert info["mode"].startswith("limited")
    ids = {c["chunk_id"] for c, _ in todo}
    assert ids == {"isr3", "isr4", "isr5", "icr1", "icr2", "rev1", "p2isr"}  # latest 3 P1 ISRs; P2's only ISR
    assert info["scope_calls"] == 8                                         # icr2 has 2 calls (lessons + events)


def test_rerun_skips_done_and_keeps_the_same_scope():
    todo, info = lx.select_requests(CHUNKS, budget=5)
    first = todo[:5]
    done = {(c["chunk_id"], c["text_hash"], k) for c, k in first}
    todo2, info2 = lx.select_requests(CHUNKS, done=done, budget=5)
    assert info2["mode"] == info["mode"]                                    # scope decided on the full total
    assert info2["already_done"] == 5 and info2["calls_this_run"] == info["scope_calls"] - 5
    assert not {(c["chunk_id"], k) for c, k in todo2} & {(c["chunk_id"], k) for c, k in first}
    # a chunk whose text changed (new text_hash) is processed again
    changed = [dict(c, text_hash="new") if c["chunk_id"] == "icr1" else c for c in CHUNKS]
    todo3, _ = lx.select_requests(changed, done=done | {("icr1", "hicr1", "events")}, budget=5)
    assert ("icr1", "events") in {(c["chunk_id"], k) for c, k in todo3}


def test_batches():
    assert [len(b) for b in lx.batches(list(range(250)), 100)] == [100, 100, 50]
