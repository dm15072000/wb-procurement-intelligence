"""Guardrails: every claim must be anchored in a source we gave the model (docs/03_model_rag_design.md §4).

Plain-language summary: the language model must answer in a fixed format: a list of claims, each citing the IDs of
the evidence passages (E1, E2, ...) or structured facts (F1, F2, ...) it relies on. Then this module checks each claim
mechanically, without trusting the model:
  * it cites at least one source, and every cited ID is one we actually supplied;
  * every number in the claim (amounts, percentages, durations, years) appears in a cited source;
  * every project ID (P + 6 digits) in the claim appears in a cited source.
A claim that fails is removed, and the removal is reported. The same idea checks LLM extractions: an extracted lesson
or event must quote its passage word for word.
"""
import re

_NUM = re.compile(r"(?<![A-Za-z0-9])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|percent)?", re.I)
_PROJECT = re.compile(r"\bP\d{6}\b")
_CITE = re.compile(r"^[EF]\d+$")
# Numbers this small carry no factual risk on their own ("2 contracts", "phase 1"); they are not checked.
MIN_CHECKED_NUMBER = 10

INJECTION_HINTS = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|prompts?)|disregard (the|your) (instructions|rules)|"
    r"you are now|system prompt|new instructions:|do not cite|reveal your", re.I)


def numbers_in(text):
    """Normalised numbers in a text: '1,200' -> '1200', '12.5 %' -> '12.5'. Years are numbers too."""
    out = set()
    for m in _NUM.finditer(text or ""):
        whole = m.group(1).replace(",", "")
        out.add(whole + ("." + m.group(2) if m.group(2) else ""))
    return out


def _checked_numbers(claim_text):
    nums = set()
    for n in numbers_in(claim_text):
        try:
            if float(n) >= MIN_CHECKED_NUMBER:
                nums.add(n)
        except ValueError:
            pass
    return nums


def _num_supported(n, source_numbers):
    """A claimed number is supported if it appears in the sources ('12' matches a source '12.0'). Conversions are not
    inferred: '4.5 million' in a claim does not match '4,500,000' in a source. The prompt tells the model to copy
    numbers exactly as written."""
    if n in source_numbers:
        return True
    try:
        return any(float(n) == float(s) for s in source_numbers)
    except ValueError:
        return False


def check_claim(claim, sources):
    """claim: {"text", "citations": [...]}; sources: {id: text}. Returns a verdict dict with the failed checks."""
    cites = [c.strip() for c in claim.get("citations") or [] if isinstance(c, str)]
    problems = []
    if not cites:
        problems.append("no_citation")
    unknown = [c for c in cites if c not in sources or not _CITE.match(c)]
    if unknown:
        problems.append("unknown_citation:" + ",".join(unknown))
    cited_text = " ".join(sources[c] for c in cites if c in sources)
    src_nums = numbers_in(cited_text)
    missing_nums = sorted(n for n in _checked_numbers(claim.get("text")) if not _num_supported(n, src_nums))
    if missing_nums:
        problems.append("unsupported_numbers:" + ",".join(missing_nums))
    missing_ids = sorted(set(_PROJECT.findall(claim.get("text") or "")) - set(_PROJECT.findall(cited_text)))
    if missing_ids:
        problems.append("unsupported_ids:" + ",".join(missing_ids))
    return {"text": claim.get("text"), "citations": cites, "ok": not problems, "problems": problems}


def check_answer(answer, sources):
    """answer: parsed model output {"claims": [...], "insufficient_evidence": bool}. Returns
    {"claims": kept claims, "removed": failed claims with problems, "report": counts}."""
    verdicts = [check_claim(c, sources) for c in (answer or {}).get("claims") or [] if isinstance(c, dict)]
    kept = [v for v in verdicts if v["ok"]]
    removed = [v for v in verdicts if not v["ok"]]
    n_cites = sum(len(v["citations"]) for v in verdicts)
    valid_cites = sum(1 for v in verdicts for c in v["citations"] if c in sources)
    report = {"claims": len(verdicts), "kept": len(kept), "removed": len(removed),
              "citation_validity": (valid_cites / n_cites) if n_cites else None,
              "insufficient_evidence": bool((answer or {}).get("insufficient_evidence")),
              "problems": [p for v in removed for p in v["problems"]]}
    return {"claims": kept, "removed": removed, "report": report}


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def quote_in_text(quote, text, min_chars=20):
    """Extraction check: the quote must appear word for word in the passage (case and whitespace ignored) and be long
    enough to mean something. Table bars are ignored so a quote across cells still matches."""
    q, t = _norm(quote).replace("|", " "), _norm(text).replace("|", " ")
    q, t = re.sub(r"\s+", " ", q).strip(), re.sub(r"\s+", " ", t)
    return len(q) >= min_chars and q in t


def injection_flags(text):
    """Instruction-like phrases inside retrieved text. They are reported (the text is data and is never obeyed);
    the prompt also wraps evidence so it cannot pose as instructions."""
    return sorted({m.group(0).lower() for m in INJECTION_HINTS.finditer(text or "")})


def retrieval_gate(hits, min_score):
    """True if the best retrieval score clears the threshold (calibrated on the evaluation set)."""
    return bool(hits) and max(h.get("score", 0.0) for h in hits) >= min_score
