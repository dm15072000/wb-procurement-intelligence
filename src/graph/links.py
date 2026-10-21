"""Contract -> document-chunk links (docs/02_data_model.md §3).

Plain-language summary: a contract row and an ICR/ISR passage share no key except the project. So we link them only
when there is evidence, and we score the link instead of asserting it.
  required:  same project, and the document is dated on/after the contract signing (it can describe it);
  strong:    the passage mentions the contract's reference number or its supplier's name  -> DESCRIBES edge;
  weak:      the passage shares at least 3 RARE words with the contract description       -> MAY_DESCRIBE edge.
"Rare" is measured, not guessed: a word counts only if it appears in at most RARE_DF_SHARE of all chunks, so common
words ("rural", "road", district names) carry no weight. First full run (checkpoint c): 99.9% of links rested on
overlap of common words, so weak evidence now gets its own, lower-weight edge type.
"""
import math
import re
from collections import Counter

from src.entity_resolution.keys import ref_key

LINK_DOC_TYPES = {"Implementation Completion and Results Report", "Implementation Status and Results Report",
                  "Implementation Completion Report Review", "Project Performance Assessment Report", "Project Paper"}
SECTION_BONUS_WORDS = ("lesson", "key factor", "procurement", "implementation", "contract")
STOP = set("""about above after again against among because before being below between construction contract contracts
consultant consulting consultancy design works services supply project road roads works management supervision
services package phase lot district state national""".split())
RARE_DF_SHARE = 0.01     # a word is "rare" if it appears in <= 1% of chunks
MIN_RARE_SHARED = 3      # weak link needs at least this many shared rare words
STRONG, WEAK = "DESCRIBES", "MAY_DESCRIBE"


def _words(text):
    return {w for w in re.findall(r"[a-z]{5,}", (text or "").lower()) if w not in STOP}


def chunk_features(chunk):
    """Text features computed ONCE per chunk and reused for every contract it is compared with."""
    text = chunk.get("text") or ""
    return {"_refnorm": ref_key(text) or "", "_upper": " " + re.sub(r"[^A-Z0-9 ]", " ", text.upper()) + " ",
            "_words": _words(text)}


def document_frequencies(chunks):
    """(df Counter: word -> number of chunks containing it, N chunks). Computed over ALL chunks of the corpus."""
    df = Counter()
    for c in chunks:
        df.update(c["_words"] if "_words" in c else _words(c.get("text")))
    return df, len(chunks)


def rare_shared(description, chunk_words, df=None, n_chunks=None):
    """Shared words that are rare in the corpus, with their IDF weight log(N / df). Without df (unit tests), every
    shared word counts as rare with weight 1."""
    shared = _words(description) & chunk_words
    if df is None or not n_chunks:
        return {w: 1.0 for w in shared}
    limit = max(1, int(RARE_DF_SHARE * n_chunks))
    return {w: math.log(n_chunks / max(df[w], 1)) for w in shared if df[w] <= limit}


def link_score(contract, chunk, supplier_names=(), df=None, n_chunks=None):
    """Return (score, rule, edge_type) or (0.0, None, None).
    contract: project_id, signing_date, ref_key, description. chunk: project_id, doc_type, doc_date, section_path, text.
    """
    if not contract.get("project_id") or contract.get("project_id") != chunk.get("project_id"):
        return 0.0, None, None
    if chunk.get("doc_type") not in LINK_DOC_TYPES:
        return 0.0, None, None
    if not (contract.get("signing_date") and chunk.get("doc_date") and chunk["doc_date"] >= contract["signing_date"]):
        return 0.0, None, None  # temporal rule: a document can only describe a contract signed before it
    f = chunk if "_refnorm" in chunk else dict(chunk, **chunk_features(chunk))
    evidence, score = [], 0.3
    rk = contract.get("ref_key")
    if rk and len(rk) >= 6 and rk in f["_refnorm"]:
        evidence.append("ref_mention")
        score += 0.4
    for name in supplier_names:
        if name and len(name) >= 6 and f" {name} " in f["_upper"]:
            evidence.append("supplier_mention")
            score += 0.2
            break
    rare = rare_shared(contract.get("description"), f["_words"], df, n_chunks)
    if len(rare) >= MIN_RARE_SHARED:
        evidence.append(f"rare_words:{len(rare)}")
        score += 0.1
    if not evidence:
        return 0.0, None, None
    strong = any(e in ("ref_mention", "supplier_mention") for e in evidence)
    if any(w in (chunk.get("section_path") or "").lower() for w in SECTION_BONUS_WORDS):
        score += 0.1
    if not strong:
        score = min(score, 0.45)   # a weak link always weighs less than any strong one
    return round(min(score, 1.0), 2), "project+temporal+" + "+".join(evidence), STRONG if strong else WEAK
