"""Evaluation-set helpers (checkpoint d changes 3-5, 2026-10-21). Unit-tested.

Plain-language summary:
  * Synthetic questions: when the LLM writes a question from a passage, it tends to copy the passage's own words, which
    makes retrieval look easier than it is. The prompt therefore asks for a paraphrase that avoids the passage's key
    phrases, and we MEASURE how much wording the question still shares with the passage (lexical overlap); questions
    that still copy too much are regenerated once and flagged if they remain too close.
  * Split: the refusal threshold is chosen on one half of the questions (calibration) and the metrics are reported on
    the other half (test), so the reported numbers are not tuned on the questions they describe. The split is
    deterministic (a hash of the question ID) and stratified by question kind.
"""
import hashlib
import re
from collections import defaultdict

QUESTION_SYSTEM = "You write evaluation questions for a document search system. Reply with one question only."

QUESTION_PROMPTS = {
    "lesson": ("Write one specific question that a procurement specialist planning a new road project could ask, and "
               "that this passage answers."),
    "tender_design": ("Write one specific question that a team drafting the bidding documents or terms of reference "
                      "for a new road contract could ask (for example about qualification criteria, experience or "
                      "turnover requirements, scope, or packaging), and that this passage answers."),
}
PARAPHRASE_RULES = ("Rules: paraphrase in your own words; do NOT reuse the passage's key phrases, names of places or "
                    "firms, reference numbers or distinctive terms; do not mention 'the passage', a document, or a "
                    "project ID. Ask the way a practitioner would, before knowing this passage exists.")
MAX_OVERLAP = 0.5   # share of the question's content words found in the passage above which it is regenerated

_STOP = set("the a an of and or for to in on at by with from what which who how did does do was were is are be been "
            "this that these those about any all can could should would will new road roads project projects "
            "contract contracts".split())


def question_messages(kind, passage, max_chars=3000):
    return [{"role": "system", "content": QUESTION_SYSTEM},
            {"role": "user", "content": f"{QUESTION_PROMPTS[kind]}\n{PARAPHRASE_RULES}\n<passage>\n"
                                        f"{re.sub(r'</?passage[^>]*>', '', passage or '')[:max_chars]}\n</passage>"}]


def content_words(text):
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}


def lexical_overlap(question, passage):
    """Share of the question's content words that also appear in the passage (0 = fully paraphrased)."""
    q = content_words(question)
    return round(len(q & content_words(passage)) / len(q), 3) if q else 0.0


def generate_question(chat, kind, passage):
    """Ask once; if the question copies too much of the passage, ask once more and keep the less-overlapping one.
    Returns (question, overlap)."""
    best = None
    for _ in range(2):
        q = (chat(question_messages(kind, passage)) or "").strip().strip('"').split("\n")[0]
        ov = lexical_overlap(q, passage)
        if best is None or ov < best[1]:
            best = (q, ov)
        if ov <= MAX_OVERLAP:
            break
    return best


def split_half(questions, key="qid", strat="kind", group=None):
    """Deterministic, stratified 50/50 split -> (calibration, test). Within each kind, questions are ordered by a hash
    of their ID and alternated, so each half gets about half of every kind.

    group (e.g. "project_id"): questions sharing a group value always land in the SAME half (2026-10-22 review: an
    ICR and its IEG review state the same lesson, and two questions from neighbouring passages of one report are near
    duplicates; calibrating on one and testing on the other leaks). Whole groups, ordered by hash, go to the half with
    fewer questions so far. Questions with no group value form their own group."""
    by_kind = defaultdict(list)
    for q in questions:
        by_kind[q.get(strat)].append(q)
    calib, test = [], []
    h = lambda x: hashlib.sha1(str(x).encode()).hexdigest()
    for kind in sorted(by_kind, key=str):
        if group is None:
            ordered = sorted(by_kind[kind], key=lambda q: h(q[key]))
            for i, q in enumerate(ordered):
                (calib if i % 2 == 0 else test).append(q)
            continue
        groups = defaultdict(list)
        for q in by_kind[kind]:
            groups[q.get(group) or f"__{q[key]}"].append(q)
        n_c = n_t = 0
        for g in sorted(groups, key=lambda g: (-len(groups[g]), h(g))):     # big groups first, then by hash
            if n_c <= n_t:
                calib += groups[g]; n_c += len(groups[g])
            else:
                test += groups[g]; n_t += len(groups[g])
    return calib, test


def calibrate_threshold(rows, min_pass=0.90, grid=None):
    """Highest gate threshold that still lets at least min_pass of the ANSWERABLE calibration questions through
    (identifier hits always pass). rows: {"top_score", "identifier_hit", "should_refuse"}."""
    grid = grid or [round(0.30 + 0.01 * i, 2) for i in range(51)]
    answerable = [r for r in rows if not r.get("should_refuse")]

    def passes(th, rs):
        return sum(r["top_score"] >= th or bool(r.get("identifier_hit")) for r in rs) / max(1, len(rs))
    ok = [th for th in grid if passes(th, answerable) >= min_pass]
    th = max(ok) if ok else grid[0]
    refusals = [r for r in rows if r.get("should_refuse")]
    return th, {"threshold": th, "answerable_passing": passes(th, answerable),
                "refusals_blocked_by_gate": (1 - passes(th, refusals)) if refusals else None,
                "n_calibration": len(rows)}
