"""Exact and near-duplicate chunk flags (keep the LATEST as primary, keep older copies searchable by date).

Plain-language summary: ISRs are written every ~6 months and repeat the same blocks (rating tables, PDO text) with
small changes such as dates or one rating. If all copies are searched equally, they crowd out everything else. So:
  * exact repeats (same text, any document type, same project) and
  * near repeats (ISRs only: same project and section, ~same words once numbers and dates are masked)
are grouped. In each group the copy from the LATEST document is primary; older copies are flagged as duplicates but
NOT deleted. Default retrieval can use primaries only, and a question like "what did the ISR say in 2019?" can still
filter on doc_date and find the older copy.
"""
import re
from collections import defaultdict

from src.chunking.sections import text_hash

NEAR_DOC_TYPES = {"Implementation Status and Results Report"}
NEAR_THRESHOLD = 0.85   # Jaccard similarity of word 3-grams, after masking numbers and dates
_MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def shape(text):
    """Text with the parts that change between ISR versions masked: digits -> 0, month names -> MONTH."""
    t = (text or "").lower()
    t = re.sub(_MONTHS, " month ", t)
    t = re.sub(r"\d+", "0", t)
    return re.sub(r"\s+", " ", t).strip()


def shingles(text, n=3):
    w = shape(text).split()
    return {" ".join(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a or b else 1.0


def flag_repeats(chunks):
    """Adds dup_group (chunk_id of the group's primary), dup_kind ('exact' / 'near' / None), is_primary and
    is_duplicate to every chunk dict (in place). Needs: chunk_id, project_id, doc_type, doc_date, section_path, text."""
    # Latest document first, so the first member of every group is the primary.
    order = sorted(chunks, key=lambda c: (str(c.get("doc_date") or ""), c["chunk_id"]), reverse=True)
    for c in order:
        c.update(dup_group=c["chunk_id"], dup_kind=None, is_primary=True, is_duplicate=False)

    exact_primary = {}
    for c in order:
        k = (c.get("project_id"), c.get("text_hash") or text_hash(c["text"]))
        if k in exact_primary:
            c.update(dup_group=exact_primary[k]["chunk_id"], dup_kind="exact", is_primary=False, is_duplicate=True)
        else:
            exact_primary[k] = c

    groups = defaultdict(list)   # (project, doc_type, section) -> list of (primary chunk, its shingles)
    for c in order:
        if c["is_duplicate"] or c.get("doc_type") not in NEAR_DOC_TYPES:
            continue
        key = (c.get("project_id"), c.get("doc_type"), c.get("section_path") or "")
        sh = shingles(c["text"])
        match = next((p for p, psh in groups[key] if p["doc_id"] != c["doc_id"] and jaccard(sh, psh) >= NEAR_THRESHOLD), None)
        if match:
            c.update(dup_group=match["chunk_id"], dup_kind="near", is_primary=False, is_duplicate=True)
        else:
            groups[key].append((c, sh))
    return chunks
