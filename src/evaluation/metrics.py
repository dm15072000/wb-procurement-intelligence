"""Retrieval and answer metrics for the RAG evaluation (docs/03_model_rag_design.md §3c). Unit-tested.

Plain-language summary: for each evaluation question we know which passage(s) hold the answer ("gold"). Recall@k asks
"was a gold passage among the first k retrieved?"; MRR rewards finding it near the top. Refusal accuracy asks "did
the system refuse exactly when it should?". Everything is averaged overall and per country.
"""
from collections import defaultdict


def recall_at_k(retrieved, gold, k):
    """1.0 if any gold id is in the top k, else 0.0 (None when the question has no gold, e.g. refusal cases)."""
    if not gold:
        return None
    return float(bool(set(retrieved[:k]) & set(gold)))


def reciprocal_rank(retrieved, gold):
    if not gold:
        return None
    for i, r in enumerate(retrieved, 1):
        if r in gold:
            return 1.0 / i
    return 0.0


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def summarise(rows, group_key=None):
    """rows: one per question with keys retrieved, gold, should_refuse, status, guardrail_report, (group_key).
    Returns {group: metrics}; group 'all' is always present."""
    groups = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        if group_key:
            groups[r.get(group_key) or "unknown"].append(r)
    out = {}
    for g, rs in groups.items():
        answerable = [r for r in rs if not r.get("should_refuse")]
        refusal = [r for r in rs if r.get("should_refuse")]
        reports = [r.get("guardrail_report") or {} for r in answerable]
        out[g] = {
            "n": len(rs),
            "recall@5": mean(recall_at_k(r["retrieved"], r["gold"], 5) for r in answerable),
            "recall@10": mean(recall_at_k(r["retrieved"], r["gold"], 10) for r in answerable),
            "mrr": mean(reciprocal_rank(r["retrieved"], r["gold"]) for r in answerable),
            "citation_validity": mean(rep.get("citation_validity") for rep in reports),
            "claim_support_rate": mean((rep["kept"] / rep["claims"]) if rep.get("claims") else None for rep in reports),
            "answered_share": mean(float(r["status"] == "answered") for r in answerable),
            "refusal_accuracy": mean(float(r["status"] != "answered") for r in refusal),
        }
    return out
