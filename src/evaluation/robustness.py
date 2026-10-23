"""Phrasing robustness (2026-10-23): the same question asked plainly and with an answer-style instruction ("in
detail") must retrieve the same passages and give answers that agree. Unit-tested.

Plain-language summary: a user who adds "tell me in detail" should get a longer answer, not a different one. For each
question we compare the two runs' top-5 retrieved passages (overlap) and ask an LLM judge whether the two answers
state the same facts (one may be more detailed).
"""
STYLE_PREFIX = "Tell me in detail: "

JUDGE_SYSTEM = ("You compare two answers to the same question. Reply YES if they agree on the facts (one may be more "
                "detailed or phrased differently), NO if they contradict each other or describe different things. "
                "Reply YES or NO only.")


def variants(question):
    return question, STYLE_PREFIX + question


def overlap(a, b, k=5):
    """Jaccard overlap of the top-k retrieved chunk IDs (1.0 = the same passages)."""
    sa, sb = set((a or [])[:k]), set((b or [])[:k])
    if not sa and not sb:
        return 1.0
    return round(len(sa & sb) / len(sa | sb), 3)


def judge_messages(question, answer_a, answer_b):
    return [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": f"QUESTION: {question}\n\nANSWER A:\n{answer_a or '(no answer)'}\n\n"
                                        f"ANSWER B:\n{answer_b or '(no answer)'}"}]


def parse_judge(reply):
    t = (reply or "").strip().upper()
    if t.startswith("YES"):
        return True
    if t.startswith("NO"):
        return False
    return None


def summarise(rows):
    """rows: {"qid", "overlap", "same_status", "agree"} -> summary for the results table."""
    n = len(rows)
    agree = [r["agree"] for r in rows if r["agree"] is not None]
    return {"n": n,
            "identical_top5": sum(r["overlap"] == 1.0 for r in rows),
            "mean_top5_overlap": round(sum(r["overlap"] for r in rows) / n, 3) if n else None,
            "same_status": sum(bool(r["same_status"]) for r in rows),
            "answers_agree": sum(agree), "answers_judged": len(agree),
            "per_question": rows}
