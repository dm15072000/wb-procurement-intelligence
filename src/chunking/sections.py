"""Section-aware chunking of ai_parse_document output (requirement M1.5).

Plain-language summary: long reports (PADs, ICRs: 40-150 pages) are cut into pieces small enough to search and cite,
but cut along the document's own structure:
  * page headers, footers and page numbers are dropped (boilerplate);
  * headings start a new section, and every chunk remembers its heading path ("V. LESSONS > Procurement");
  * tables become their own chunk with the section path attached; a very large table is split into row groups, each
    repeating the header row, so every part can be read and cited on its own;
  * text is packed into chunks of about TARGET_TOKENS, with a short overlap so a sentence at a boundary isn't lost;
  * pieces that are too small to be useful (run 1 on real data: median 53 tokens) are merged with their neighbours in
    the same section, or in sibling sections under the same parent heading, keeping every heading for citation.
"""
import hashlib
import json
import re

DROP_TYPES = {"page_header", "page_footer", "page_number"}
HEADING_TYPES = {"title", "section_header"}
TARGET_TOKENS = 600
MAX_TOKENS = 900
OVERLAP_TOKENS = 60
MIN_TOKENS = 150          # chunks smaller than this get merged with neighbours where the structure allows
TABLE_SPLIT_TOKENS = 800  # tables larger than this are split into row groups (header repeated)
# Embedding model limit: databricks-gte-large-en reads 8,192 tokens (verified, Databricks docs, 2026-10-21). Our token
# count is an ESTIMATE (words x 1.3) and numbers tokenize worse than words, so a hard character cap is the safety net:
# even at a pessimistic ~1.25 characters per token, 10,000 characters stays under 8,192 tokens.
EMBED_TOKEN_LIMIT = 8192
MAX_CHARS = 10_000
_ROMAN = re.compile(r"^(?=[IVXLC]+\.)[IVXLC]+\.\s")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s")
_LETTER = re.compile(r"^[A-Z][.)]\s")


def est_tokens(text):
    """Rough token estimate (~1.3 tokens per word): good enough for sizing chunks. Pure-punctuation tokens such as the
    ' | ' cell separators in table text are not counted (run 1 overstated table sizes because they were)."""
    return int(sum(1 for w in (text or "").split() if any(ch.isalnum() for ch in w)) * 1.3) + 1


def text_hash(text):
    """Fingerprint of a chunk's text (case/space-insensitive), for finding exact repeats such as ISR rating tables."""
    return hashlib.sha1(re.sub(r"\s+", " ", (text or "").lower()).strip().encode("utf-8")).hexdigest()[:16]


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
                       "page_end": max(pages) if pages else None, "n_tokens": est_tokens(text),
                       "_path": list(path)})

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
    final = enforce_char_cap(merge_small(split_large_tables(final)))
    for i, ch in enumerate(final):
        ch["seq"], ch["chunk_id"] = i, f"{doc_meta['doc_id']}-{i:04d}"
        ch["text_hash"] = text_hash(ch["text"])
        ch.setdefault("table_part", None)
        ch.setdefault("merged_sections", 1)
        ch.pop("_path", None)
    return final


def split_large_tables(chunks):
    """Split table chunks above TABLE_SPLIT_TOKENS into row groups of about TARGET_TOKENS, repeating the header row
    (the first line) at the top of every part. Parts are labelled 'k/n' in table_part."""
    out = []
    for ch in chunks:
        if ch["chunk_type"] != "table" or ch["n_tokens"] <= TABLE_SPLIT_TOKENS:
            out.append(ch)
            continue
        lines = ch["text"].split("\n")
        header, rows = lines[0], lines[1:]
        groups, cur = [], []
        for r in rows:
            if cur and est_tokens("\n".join([header] + cur + [r])) > TARGET_TOKENS:
                groups.append(cur)
                cur = []
            cur.append(r)
        if cur:
            groups.append(cur)
        for k, g in enumerate(groups, 1):
            text = "\n".join([header] + g)
            out.append(dict(ch, text=text, n_tokens=est_tokens(text), table_part=f"{k}/{len(groups)}"))
    return out


def enforce_char_cap(chunks, max_chars=MAX_CHARS):
    """Last safety net for the embedding limit: any chunk longer than max_chars (e.g. one enormous table row, or text
    with no sentence breaks) is cut at line or space boundaries into pieces <= max_chars. Labelled in table_part."""
    out = []
    for ch in chunks:
        if len(ch["text"]) <= max_chars:
            out.append(ch)
            continue
        pieces, rest = [], ch["text"]
        while len(rest) > max_chars:
            cut = max(rest.rfind("\n", 0, max_chars), rest.rfind(" ", 0, max_chars))
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        pieces.append(rest)
        for k, p in enumerate(pieces, 1):
            out.append(dict(ch, text=p, n_tokens=est_tokens(p), table_part=f"cap {k}/{len(pieces)}"))
    return out


def _mergeable(a, b):
    """Two neighbouring chunks may merge if neither is a split-table part, the result stays within TARGET_TOKENS, and
    they share a section, or are sibling sections under the same parent heading (never across top-level sections)."""
    if a.get("table_part") or b.get("table_part"):
        return False
    if a["n_tokens"] + b["n_tokens"] > TARGET_TOKENS:
        return False
    pa, pb = a.get("_path") or [], b.get("_path") or []
    if pa == pb:
        return True
    return len(pa) >= 2 and len(pb) >= 2 and pa[:-1] == pb[:-1] or (len(pa) >= 1 and pb[:len(pa)] == pa)


def merge_small(chunks):
    """Merge chunks below MIN_TOKENS into their following neighbour when _mergeable. The merged chunk keeps the first
    piece's section path and context header; every heading inside it is written into the text, so nothing is lost for
    citation. chunk_type becomes 'mixed' when text and table pieces are combined."""
    out = []
    for ch in chunks:
        prev = out[-1] if out else None
        if prev and (prev["n_tokens"] < MIN_TOKENS or ch["n_tokens"] < MIN_TOKENS) and _mergeable(prev, ch):
            heading = ch.get("_path") or []
            label = heading[-1] if heading and heading != (prev.get("_path") or []) else None
            joined = prev["text"] + "\n\n" + (f"[{label}]\n" if label else "") + ch["text"]
            pages = [p for p in (prev["page_start"], prev["page_end"], ch["page_start"], ch["page_end"]) if p is not None]
            out[-1] = dict(prev, text=joined, n_tokens=est_tokens(joined),
                           chunk_type=prev["chunk_type"] if prev["chunk_type"] == ch["chunk_type"] else "mixed",
                           page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
                           merged_sections=prev.get("merged_sections", 1) + (1 if label else 0))
        else:
            out.append(dict(ch))
    return out
