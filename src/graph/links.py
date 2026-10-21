"""Contract -> document-section links ("DESCRIBES" edges, docs/02_data_model.md §3).

Plain-language summary: a contract row and an ICR/ISR passage share no key except the project. So we link them only
when there is evidence, and we score the link instead of asserting it:
  required: same project, and the document is dated on/after the contract signing (it can describe it);
  evidence: the passage mentions the contract's reference number, its supplier's name, or distinctive words from its
  description; passages in lessons / key-factor / procurement sections weigh a little more.
A link with no text evidence is not created, otherwise every contract would link to every passage of its project.
"""
import re

from src.entity_resolution.keys import ref_key

LINK_DOC_TYPES = {"Implementation Completion and Results Report", "Implementation Status and Results Report",
                  "Implementation Completion Report Review", "Project Performance Assessment Report", "Project Paper"}
SECTION_BONUS_WORDS = ("lesson", "key factor", "procurement", "implementation", "contract")
STOP = set("""about above after again against among because before being below between construction contract contracts
consultant consulting consultancy design works services supply project road roads works management supervision
services package phase lot district state national""".split())
MIN_SCORE = 0.5


def _words(text):
    return {w for w in re.findall(r"[a-z]{5,}", (text or "").lower()) if w not in STOP}


def chunk_features(chunk):
    """Text features computed ONCE per chunk and reused for every contract it is compared with."""
    text = chunk.get("text") or ""
    return {"_refnorm": ref_key(text) or "", "_upper": " " + re.sub(r"[^A-Z0-9 ]", " ", text.upper()) + " ",
            "_words": _words(text)}


def link_score(contract, chunk, supplier_names=()):
    """Return (score, rule) or (0.0, None). contract: project_id, signing_date, ref_key, borrower_reference,
    description. chunk: project_id, doc_type, doc_date, section_path, text."""
    if not contract.get("project_id") or contract.get("project_id") != chunk.get("project_id"):
        return 0.0, None
    if chunk.get("doc_type") not in LINK_DOC_TYPES:
        return 0.0, None
    if not (contract.get("signing_date") and chunk.get("doc_date") and chunk["doc_date"] >= contract["signing_date"]):
        return 0.0, None  # temporal rule: a document can only describe a contract signed before it
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
    shared = _words(contract.get("description")) & f["_words"]
    if len(shared) >= 2:
        evidence.append("description_overlap")
        score += 0.1
    if not evidence:
        return 0.0, None
    if any(w in (chunk.get("section_path") or "").lower() for w in SECTION_BONUS_WORDS):
        score += 0.1
    score = round(min(score, 1.0), 2)
    if score < MIN_SCORE:
        return 0.0, None
    return score, "project+temporal+" + "+".join(evidence)
