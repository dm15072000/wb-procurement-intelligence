"""Procurement notice text as retrieval chunks (checkpoint d change 3, 2026-10-21). Unit-tested.

Plain-language summary: Requests for Expressions of Interest and Invitations for Bids describe what was bought and
what bidders had to prove (experience, turnover, key staff): exactly what a team designing a new tender (L2) wants to
compare against. Notice text comes from the procnotices API as plain text or light HTML. Each notice becomes one or
more chunks with doc_type "Procurement Notice", so it is searched and cited like a report passage. The citation is the
notice ID and its reference number (no URL is invented).
"""
import html
import re

from src.chunking.sections import MAX_CHARS, est_tokens, text_hash

NOTICE_DOC_TYPE = "Procurement Notice"
INDEXED_NOTICE_TYPES = ("Request for Expression of Interest", "Invitation for Bids", "Invitation for Prequalification")
TARGET_CHARS = 3500   # ~600 tokens of prose, like report chunks


def clean_text(raw):
    """Strip tags, decode entities, keep paragraph breaks."""
    t = re.sub(r"(?i)<\s*(br|/p|/div|/li|/tr|/h\d)\s*/?>", "\n", raw or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def _split(text, size=TARGET_CHARS):
    """Paragraph-aware pieces of at most `size` characters (a single huge paragraph is cut at spaces)."""
    parts, cur = [], ""
    for para in text.split("\n\n"):
        while len(para) > size:
            cut = para.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        if cur and len(cur) + len(para) + 2 > size:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


def notice_chunks(n, min_chars=200):
    """silver.notices row -> chunk dicts in the docs.chunks shape (plus title). Notices of other types, without text,
    or with very little text are skipped."""
    if n.get("notice_type") not in INDEXED_NOTICE_TYPES:
        return []
    text = clean_text(n.get("notice_text"))
    if len(text) < min_chars:
        return []
    doc_id = f"notice-{n['notice_id']}"
    section = f"{n['notice_type']} > {n.get('bid_reference_no') or 'no reference'}"
    header = f"[{NOTICE_DOC_TYPE} | {n.get('project_id')} | {n.get('notice_date')}] {section}"
    out = []
    for i, piece in enumerate(_split(text)):
        piece = piece[:MAX_CHARS]
        out.append({"chunk_id": f"{doc_id}-{i:04d}", "doc_id": doc_id, "seq": i, "chunk_type": "text",
                    "section_path": section, "text": piece, "context_header": header, "page_start": None,
                    "page_end": None, "n_tokens": est_tokens(piece), "text_hash": text_hash(piece),
                    "table_part": None, "merged_sections": 1, "project_id": n.get("project_id"),
                    "doc_type": NOTICE_DOC_TYPE, "doc_date": n.get("notice_date"),
                    "title": (n.get("bid_description") or section)[:300], "notice_id": n["notice_id"]})
    return out
