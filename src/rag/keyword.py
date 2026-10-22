"""Keyword retrieval for hybrid search (checkpoint d change 2, 2026-10-21). Unit-tested.

Plain-language summary: embeddings are good at meaning and bad at codes. "GYTJ201B" or "P147864" means little to an
embedding model, but a passage that contains that exact code is exactly what the user wants. So retrieval also does
keyword matching:
  * identifiers in the query (project IDs, contract / bid references such as "HAMP-CS-01") are matched as whole
    codes, ignoring dashes and spaces ("HAMP CS 01" matches "HAMP-CS-01", but not inside "HAMP-CS-012");
  * other words are scored with BM25, the standard keyword-ranking formula.
Databricks Vector Search does this itself in HYBRID mode; this module is the fallback when hybrid mode is not
available for the index, and it is also used to mark which hits contain a queried identifier (for the gate and rerank).
"""
import math
import re
import unicodedata
from collections import Counter

from src.rag.retrieval import matches

_PID = re.compile(r"\bP\d{6}\b", re.I)
# a code: 2+ parts joined by - / . _ or one token mixing letters and digits; at least 5 alphanumerics in total
_CODE = re.compile(r"\b[A-Z0-9]+(?:[-/._][A-Z0-9]+)+\b|\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,}\b", re.I)
_STOP = set("the a an of and or for to in on at by with from what which who how did does was were is are be been "
            "this that these those about any all past project projects contract contracts report reports".split())
K1, B = 1.5, 0.75


def _fold(text):
    d = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in d if not unicodedata.combining(ch)).lower()


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]{2,}", _fold(text)) if t not in _STOP]


_NOT_IDS = re.compile(r"^(FY\d{2,4}|COVID\d*|Q[1-4]\d{4}|\d+[A-Z]{1,2})$")   # fiscal years, COVID-19, '2019Q1', '30TH'


def identifiers(query):
    """Project IDs and reference codes in a query, normalised to A-Z0-9 ('HAMP-CS-01' -> 'HAMPCS01'). A code needs at
    least 5 characters with both a letter and a digit, so dates, years and plain numbers are not identifiers."""
    out = []
    for m in list(_PID.finditer(query or "")) + list(_CODE.finditer(query or "")):
        norm = re.sub(r"[^A-Z0-9]", "", m.group(0).upper())
        if (len(norm) >= 5 and any(ch.isdigit() for ch in norm) and any(ch.isalpha() for ch in norm)
                and not _NOT_IDS.match(norm) and norm not in out):
            out.append(norm)
    return out


def _id_regex(norm):
    body = r"[\s\-/_.]*".join(re.escape(ch) for ch in norm)
    return re.compile(r"(?<![A-Z0-9])" + body + r"(?![A-Z0-9])", re.I)


def id_match(text, ids):
    """The first queried identifier found in the text as a whole code, or None."""
    for norm in ids:
        m = _id_regex(norm).search(text or "")
        if m:
            return m.group(0)
    return None


class KeywordIndex:
    """BM25 over chunk text plus whole-code identifier matching. rows: dicts with chunk_id, text and the filter
    columns used by retrieval.filters_for (project_id, doc_type, doc_date_int, is_primary, country_iso3)."""

    def __init__(self, rows):
        self.rows = [r for r in rows if r.get("text")]
        self.tf = [Counter(tokens(r["text"])) for r in self.rows]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter(w for t in self.tf for w in t)
        n = len(self.rows)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query, filters, k=10):
        """Top-k hits with kw_score in [0, 1] (BM25 normalised by the best hit; an identifier match scores 1.0) and
        id_match set when a queried identifier appears in the passage."""
        q = tokens(query)
        ids = identifiers(query)
        scored = []
        for i, r in enumerate(self.rows):
            if not matches(r, filters):
                continue
            tf, dl = self.tf[i], self.len[i]
            bm = sum(self.idf.get(w, 0) * tf[w] * (K1 + 1) / (tf[w] + K1 * (1 - B + B * dl / max(self.avg, 1)))
                     for w in q if w in tf)
            hit = id_match(r["text"], ids) if ids else None
            if bm > 0 or hit:
                scored.append((bm, hit, r))
        if not scored:
            return []
        top = max(s[0] for s in scored) or 1.0
        out = [dict({c: r.get(c) for c in r if c != "embedding"}, kw_score=1.0 if hit else round(bm / top, 4),
                    id_match=hit) for bm, hit, r in scored]
        return sorted(out, key=lambda h: -h["kw_score"])[:k]
