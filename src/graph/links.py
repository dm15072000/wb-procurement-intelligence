"""Contract -> document-chunk links (docs/02_data_model.md §3).

Plain-language summary: a contract row and an ICR/ISR passage share no key except the project. So we link them only
when there is evidence, and we score the link instead of asserting it.
  required:  same project, and the document is dated on/after the contract signing (it can describe it);
  strong:    the passage mentions the contract's reference number or its supplier's name  -> DESCRIBES edge;
  weak:      the passage shares at least 3 RARE words with the contract description       -> MAY_DESCRIBE edge.
"Rare" is measured, not guessed: a word counts only if it appears in at most RARE_DF_SHARE of the chunks of the SAME
project, so common words ("rural", "road", district names) carry no weight. First full run (checkpoint c): 99.9% of
links rested on overlap of common words, so weak evidence now gets its own, lower-weight edge type.

Second run review (checkpoint c; one fix pass approved 2026-10-21, no further tuning):
  - 7 of 11 strong links were false: a supplier "name" that is a person's name matched Bank staff lists. Fix A: supplier
    names must be firm-like (2+ words, not person-like), and staff / team / acronym sections never link.
  - reference numbers match on token boundaries only ("HAMPGD02" no longer matches inside "HAMPGD020"), and the matched
    text is returned so every link can be audited by eye.
  - weak links: rarity was measured over the whole corpus, which is mostly English, so everyday French words
    ("travaux", "pistes") counted as rare and every Mali contract matched every French passage. Fix B: rarity is
    measured within the project's own chunks, accents are folded before tokenizing, acronym tables are excluded.
"""
import functools
import math
import re
import unicodedata
from collections import Counter

from src.entity_resolution.keys import ref_key
from src.entity_resolution.suppliers import is_person_like

LINK_DOC_TYPES = {"Implementation Completion and Results Report", "Implementation Status and Results Report",
                  "Implementation Completion Report Review", "Project Performance Assessment Report", "Project Paper"}
SECTION_BONUS_WORDS = ("lesson", "key factor", "procurement", "implementation", "contract")
# Sections that list people or acronyms: they mention many names and words and describe no contract.
EXCLUDED_SECTION_WORDS = ("abbreviation", "acronym", "adm staff", "task team", "team composition", "staffing",
                          "bank lending and implementation support")
STOP = set("""about above after again against among because before being below between construction contract contracts
consultant consulting consultancy design works services supply project road roads works management supervision
services package phase lot district state national""".split())
RARE_DF_SHARE = 0.02     # a word is "rare" if it appears in <= 2% of the project's chunks (set once, not tuned)
MIN_RARE_SHARED = 3      # weak link needs at least this many shared rare words
STRONG, WEAK = "DESCRIBES", "MAY_DESCRIBE"
_NONE = (0.0, None, None, None)


def _fold(text):
    """Lower case with accents removed, so 'aménagement' is one word, not 'am' + 'nagement'."""
    d = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in d if not unicodedata.combining(ch)).lower()


def _words(text):
    return {w for w in re.findall(r"[a-z]{5,}", _fold(text)) if w not in STOP}


def excluded_section(section_path):
    sp = (section_path or "").lower()
    return any(w in sp for w in EXCLUDED_SECTION_WORDS)


def usable_supplier_name(name):
    """Firm names only: 6+ characters, 2+ words, not person-like ('SANGEETA KUMARI' would match a staff list)."""
    return bool(name) and len(name) >= 6 and len(name.split()) >= 2 and not is_person_like(name)


@functools.lru_cache(maxsize=50000)
def _ref_pattern(rk):
    """'HAMPGD02' -> regex matching 'HAMP-GD-02', 'HAMP GD 02' or 'HAMPGD02', but not inside a longer code."""
    body = r"[\s\-/_.]*".join(re.escape(ch) for ch in rk)
    return re.compile(r"(?<![A-Z0-9])" + body + r"(?![A-Z0-9])")


def chunk_features(chunk):
    """Text features computed ONCE per chunk and reused for every contract it is compared with."""
    text = chunk.get("text") or ""
    return {"_refnorm": ref_key(text) or "", "_upper": " " + re.sub(r"[^A-Z0-9 ]", " ", _fold(text).upper()) + " ",
            "_upper_raw": text.upper(), "_words": _words(text)}


def document_frequencies(chunks):
    """(df Counter: word -> number of chunks containing it, N chunks). Call it on ONE project's chunks."""
    df = Counter()
    for c in chunks:
        df.update(c["_words"] if "_words" in c else _words(c.get("text")))
    return df, len(chunks)


def rare_shared(description, chunk_words, df=None, n_chunks=None):
    """Shared words that are rare in the project, with their IDF weight log(N / df). Without df (unit tests), every
    shared word counts as rare with weight 1."""
    shared = _words(description) & chunk_words
    if df is None or not n_chunks:
        return {w: 1.0 for w in shared}
    limit = max(1, int(RARE_DF_SHARE * n_chunks))
    return {w: math.log(n_chunks / max(df[w], 1)) for w in shared if df[w] <= limit}


def link_detail(contract, chunk, supplier_names=(), df=None, n_chunks=None):
    """Return (score, rule, edge_type, matched) or (0.0, None, None, None). `matched` is the text that triggered the
    link ("ref:HAMP-GD-02", "supplier:GUIZHOU BRIDGE CONSTRUCTION GROUP", "rare:laping,liangshuijing,subgrade").
    contract: project_id, signing_date, ref_key, description. chunk: project_id, doc_type, doc_date, section_path, text.
    df / n_chunks: word document frequencies over the chunks of the contract's OWN project.
    """
    if not contract.get("project_id") or contract.get("project_id") != chunk.get("project_id"):
        return _NONE
    if chunk.get("doc_type") not in LINK_DOC_TYPES or excluded_section(chunk.get("section_path")):
        return _NONE
    if not (contract.get("signing_date") and chunk.get("doc_date") and chunk["doc_date"] >= contract["signing_date"]):
        return _NONE  # temporal rule: a document can only describe a contract signed before it
    f = chunk if "_refnorm" in chunk else dict(chunk, **chunk_features(chunk))
    evidence, matched, score = [], [], 0.3
    rk = contract.get("ref_key")
    if rk and len(rk) >= 6 and rk in f["_refnorm"]:          # cheap substring test first, then token boundaries
        m = _ref_pattern(rk).search(f["_upper_raw"])
        if m:
            evidence.append("ref_mention")
            matched.append("ref:" + m.group(0))
            score += 0.4
    for name in supplier_names:
        if usable_supplier_name(name) and f" {name} " in f["_upper"]:
            evidence.append("supplier_mention")
            matched.append("supplier:" + name)
            score += 0.2
            break
    rare = rare_shared(contract.get("description"), f["_words"], df, n_chunks)
    if len(rare) >= MIN_RARE_SHARED:
        evidence.append(f"rare_words:{len(rare)}")
        matched.append("rare:" + ",".join(sorted(rare)))
        score += 0.1
    if not evidence:
        return _NONE
    strong = any(e in ("ref_mention", "supplier_mention") for e in evidence)
    if any(w in (chunk.get("section_path") or "").lower() for w in SECTION_BONUS_WORDS):
        score += 0.1
    if not strong:
        score = min(score, 0.45)   # a weak link always weighs less than any strong one
    return (round(min(score, 1.0), 2), "project+temporal+" + "+".join(evidence), STRONG if strong else WEAK,
            " | ".join(matched))


def link_score(contract, chunk, supplier_names=(), df=None, n_chunks=None):
    """Return (score, rule, edge_type) or (0.0, None, None): link_detail without the matched text."""
    return link_detail(contract, chunk, supplier_names, df, n_chunks)[:3]
