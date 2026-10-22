"""Retrieval helpers: filters, cosine fallback, merge and rerank (docs/03_model_rag_design.md §3). Unit-tested.

Plain-language summary: the same filter dict is used by Databricks Vector Search and by the fallback (plain cosine
similarity over the embeddings table), so both paths return the same kind of result. After retrieval, results from
the project-scoped and lessons-scoped searches are merged and lightly re-ordered: similarity decides, with a small
bonus for more independent sources (IEG > ICR > ISR) and for lessons sections, and at most two passages per document
so one long report cannot fill every slot.
"""
import math
from datetime import date

LESSON_DOC_TYPES = ["Implementation Completion and Results Report", "Implementation Completion Report Review",
                    "Project Performance Assessment Report"]
AUTHORITY_BONUS = {"Project Performance Assessment Report": 0.03, "Implementation Completion Report Review": 0.03,
                   "Implementation Completion and Results Report": 0.02,
                   "Implementation Status and Results Report": 0.0, "Project Appraisal Document": 0.01}
LESSON_WORDS = ("lesson", "finding", "key factor", "procurement", "recommendation", "risk")
LESSON_BONUS = 0.03
COLUMNS = ["chunk_id", "doc_id", "project_id", "country_iso3", "doc_type", "doc_date", "section_path", "text",
           "title", "pdf_url", "page_start", "chunk_type"]


def date_int(d):
    """date or 'YYYY-MM-DD' -> 20190630 (Vector Search filters compare numbers reliably)."""
    if d is None:
        return None
    if not isinstance(d, date):
        d = date.fromisoformat(str(d)[:10])
    return d.year * 10000 + d.month * 100 + d.day


def filters_for(as_of=None, project_ids=None, doc_types=None, primary_only=True, country_iso3=None):
    """Vector Search filter dict (standard endpoint syntax). Only documents dated on/before as_of are allowed."""
    f = {}
    if as_of is not None:
        f["doc_date_int <="] = date_int(as_of)
    if project_ids:
        f["project_id"] = sorted(set(project_ids))
    if doc_types:
        f["doc_type"] = list(doc_types)
    if country_iso3:
        f["country_iso3"] = [country_iso3] if isinstance(country_iso3, str) else list(country_iso3)
    if primary_only:
        f["is_primary"] = True
    return f


def matches(row, filters):
    """Apply a filter dict to one row (the fallback path)."""
    for key, val in filters.items():
        if key.endswith(" <="):
            v = row.get(key[:-3])
            if v is None or v > val:
                return False
        elif isinstance(val, list):
            if row.get(key) not in val:
                return False
        elif row.get(key) != val:
            return False
    return True


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def cosine_search(query_vec, rows, filters, k=10):
    """Fallback retrieval: rows carry 'embedding'. Returns the top-k rows (without the embedding) with 'score'.
    Uses numpy when available, pure Python otherwise."""
    cand = [r for r in rows if matches(r, filters)]
    if not cand:
        return []
    try:
        import numpy as np
        m = np.asarray([r["embedding"] for r in cand], dtype=float)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
        q = np.asarray(query_vec, dtype=float)
        sims = m @ (q / (np.linalg.norm(q) + 1e-12))
        order = np.argsort(-sims)[:k]
        scored = [(float(sims[i]), cand[i]) for i in order]
    except ImportError:  # pragma: no cover
        q = _norm(query_vec)
        scored = sorted(((sum(a * b for a, b in zip(q, _norm(r["embedding"]))), r) for r in cand),
                        key=lambda x: -x[0])[:k]
    return [dict({c: r.get(c) for c in COLUMNS}, score=s) for s, r in scored]


def merge(*hit_lists):
    """Union by chunk_id, keeping the highest score and remembering which searches found it."""
    best = {}
    for name, hits in hit_lists:
        for h in hits:
            cur = best.get(h["chunk_id"])
            if cur is None or h.get("score", 0) > cur.get("score", 0):
                best[h["chunk_id"]] = dict(h, found_by=sorted(set((cur or {}).get("found_by", [])) | {name}))
            else:
                cur["found_by"] = sorted(set(cur.get("found_by", [])) | {name})
    return list(best.values())


def rerank(hits, k=8, per_doc=2):
    """Similarity + small authority and lessons bonuses; at most per_doc passages per document."""
    def final(h):
        sp = (h.get("section_path") or "").lower()
        bonus = AUTHORITY_BONUS.get(h.get("doc_type"), 0.0) + (LESSON_BONUS if any(w in sp for w in LESSON_WORDS) else 0)
        bonus += 0.05 if "describes_link" in (h.get("found_by") or []) else 0.0
        return h.get("score", 0.0) + bonus
    out, per = [], {}
    for h in sorted(hits, key=final, reverse=True):
        d = h.get("doc_id")
        if per.get(d, 0) >= per_doc:
            continue
        per[d] = per.get(d, 0) + 1
        out.append(dict(h, rerank_score=round(final(h), 4)))
        if len(out) >= k:
            break
    return out
