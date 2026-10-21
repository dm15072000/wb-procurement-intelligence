"""Section-aware chunking of ai_parse_document output (requirement M1.5).

Plain-language summary: long reports (PADs, ICRs: 40-150 pages) are cut into pieces small enough to search and cite,
but cut along the document's own structure:
  * page headers, footers and page numbers are dropped (boilerplate);
  * headings start a new section, and every chunk remembers its heading path ("V. LESSONS > Procurement");
  * tables are never split, and become their own chunk with the section path attached;
  * text is packed into chunks of about TARGET_TOKENS, with a short overlap so a sentence at a boundary isn't lost.
"""
import json
import re

DROP_TYPES = {"page_header", "page_footer", "page_number"}
HEADING_TYPES = {"title", "section_header"}
TARGET_TOKENS = 600
MAX_TOKENS = 900
OVERLAP_TOKENS = 60
_ROMAN = re.compile(r"^(?=[IVXLC]+\.)[IVXLC]+\.\s")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s")
_LETTER = re.compile(r"^[A-Z][.)]\s")


def est_tokens(text):
    """Rough token estimate (~1.3 tokens per word): good enough for sizing chunks."""
    return int(len((text or "").split()) * 1.3) + 1


def _page_of(el):
    for k in ("page_id", "page", "page_number"):
        if isinstance(el.get(k), int):
            return el[k]
    for b in el.get("bbox") or []:
        if isinstance(b, dict) and isinstance(b.get("page_id"), int):
            return b["page_id"]
    return None


def elements_from_parse(doc_json):
    """Ordered list of {type, content, page} from ai_parse_document JSON (string or dict). Reads
    document.elements when present; otherwise walks the structure for dicts with a 'type' key."""
    d = json.loads(doc_json) if isinstance(doc_json, str) else doc_json
    els = (d.get("document") or {}).get("elements") if isinstance(d, dict) else None
    if not isinstance(els, list):
        els, stack = [], [d]
        while stack:
            x = stack.pop(0)
            if isinstance(x, dict):
                if "type" in x and "content" in x:
                    els.append(x)
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
            elif isinstance(x, list):
                stack.extend(x)
    return [{"type": e.get("type"), "content": (e.get("content") or "").strip(), "page": _page_of(e)}
            for e in els if isinstance(e, dict) and e.get("type")]


def heading_level(text):
    """1 for 'I. OUTCOME' / all-caps headings; dotted numbers give depth ('3.2' -> 2); 'A.' -> 2; otherwise 2."""
    t = (text or "").strip()
    if _ROMAN.match(t):
        return 1
    m = _NUMBERED.match(t)
    if m:
        return min(m.group(1).count(".") + 1, 4)
    if _LETTER.match(t):
        return 2
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return 1
    return 2


def _html_to_text(html):
    """Readable text version of an HTML table: cells separated by ' | ', rows by newlines."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html or "", flags=re.S | re.I)
    if not rows:
        return re.sub(r"<[^>]+>", " ", html or "").strip()
    out = []
    for r in rows:
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S | re.I)]
        out.append(" | ".join(cells))
    return "\n".join(out)


def chunk_document(elements, doc_meta):
    """Turn ordered elements into chunks.

    doc_meta: dict with doc_id, project_id, doc_type, doc_date (used for the context header and ids).
    Returns list of chunk dicts: chunk_id, doc_id, seq, chunk_type, section_path, text, context_header,
    page_start, page_end, n_tokens.
    """
    path, chunks, buf = [], [], []

    def header():
        return f"[{doc_meta.get('doc_type')} | {doc_meta.get('project_id')} | {doc_meta.get('doc_date')}] " + " > ".join(path)

    def emit(kind, parts):
        text = "\n".join(p["content"] for p in parts).strip()
        if not text:
            return
        pages = [p["page"] for p in parts if p["page"] is not None]
        seq = len(chunks)
        chunks.append({"chunk_id": f"{doc_meta['doc_id']}-{seq:04d}", "doc_id": doc_meta["doc_id"], "seq": seq,
                       "chunk_type": kind, "section_path": " > ".join(path), "text": text,
                       "context_header": header(), "page_start": min(pages) if pages else None,
                       "page_end": max(pages) if pages else None, "n_tokens": est_tokens(text)})

    def flush(keep_overlap=False):
        nonlocal buf
        if buf:
            emit("text", buf)
            tail = buf[-1] if keep_overlap and est_tokens(buf[-1]["content"]) <= OVERLAP_TOKENS else None
            buf = [tail] if tail else []

    for el in elements:
        t, c = el.get("type"), (el.get("content") or "").strip()
        page = el["page"] if "page" in el else _page_of(el)
        if t in DROP_TYPES or not c:
            continue
        if t in HEADING_TYPES:
            flush()
            lvl = heading_level(c)
            path = path[: lvl - 1] + [c[:120]]
            continue
        if t == "table":
            flush()
            emit("table", [{"content": _html_to_text(c), "page": page}])
            continue
        if t == "figure":
            continue  # figures carry no text we can cite; captions arrive as their own 'caption' elements
        buf.append({"content": c, "page": page})
        if sum(est_tokens(b["content"]) for b in buf) >= TARGET_TOKENS:
            flush(keep_overlap=True)
    flush()
    # Hard cap: split any oversized text chunk on sentence boundaries (tables stay whole by design).
    final = []
    for ch in chunks:
        if ch["chunk_type"] == "text" and ch["n_tokens"] > MAX_TOKENS:
            sentences, part = re.split(r"(?<=[.!?])\s+", ch["text"]), []
            for s in sentences:
                part.append(s)
                if est_tokens(" ".join(part)) >= TARGET_TOKENS:
                    final.append(dict(ch, text=" ".join(part), n_tokens=est_tokens(" ".join(part))))
                    part = []
            if part:
                final.append(dict(ch, text=" ".join(part), n_tokens=est_tokens(" ".join(part))))
        else:
            final.append(ch)
    for i, ch in enumerate(final):
        ch["seq"], ch["chunk_id"] = i, f"{doc_meta['doc_id']}-{i:04d}"
    return final
