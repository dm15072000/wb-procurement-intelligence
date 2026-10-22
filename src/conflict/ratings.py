"""Ratings extraction and conflict resolution: ISR vs ICR vs IEG (docs/03_model_rag_design.md §5). Unit-tested.

Plain-language summary: a project is rated several times: in ISRs every ~6 months by the team, in the ICR at closing
(the team's self-evaluation), and in the IEG ICR Review (independent). They can disagree. We never hide that. We pick
which rating answers the question using a fixed rule based on the document timeline, and we always show the others
with their dates and flag the disagreement.

Ratings are read with regular expressions, not an LLM: they are short fixed phrases, and a regex is deterministic and
testable. Each rating keeps the exact text it came from.
"""
import re
from datetime import date

SCALE = {"highly satisfactory": 6, "satisfactory": 5, "moderately satisfactory": 4, "moderately unsatisfactory": 3,
         "unsatisfactory": 2, "highly unsatisfactory": 1}
ABBREV = {"HS": 6, "S": 5, "MS": 4, "MU": 3, "U": 2, "HU": 1}
NAME_OF = {v: k.title() for k, v in SCALE.items()}

# Label phrases -> dimension. ISR "progress towards achievement of PDO" and ICR / IEG "outcome" are one dimension.
DIMENSIONS = [
    (r"progress\s+towards?\s+(the\s+)?achievement\s+of\s+(the\s+)?(pdo|project\s+development\s+objectives?)", "outcome"),
    (r"(overall\s+)?outcome(\s+rating)?", "outcome"),
    (r"(overall\s+)?implementation\s+progress(\s*\(ip\))?", "implementation"),
    (r"procurement", "procurement"),
    (r"financial\s+management", "financial_management"),
    (r"project\s+management", "project_management"),
    (r"monitoring\s+and\s+evaluation|quality\s+of\s+m\s*&\s*e|m\s*&\s*e(\s+quality)?", "m_and_e"),
    (r"(overall\s+)?bank\s+performance", "bank_performance"),
]
_VALUE = r"(highly\s+satisfactory|moderately\s+satisfactory|moderately\s+unsatisfactory|highly\s+unsatisfactory|" \
         r"unsatisfactory|satisfactory)"
# label, then only separators / table-cell bars (no other words), then a value, then optionally a second value in the
# next table cell (ISR rating tables: "Name | Previous Rating | Current Rating").
_SEP = r"(?:[|:\-–\s]|\(\w+\)){0,12}"
_PATTERNS = [(re.compile(r"(?<![a-z])(?:" + lab + r")\s*(?:rating)?" + _SEP + _VALUE + r"(?:\s*\|\s*" + _VALUE + r")?",
                         re.I), dim) for lab, dim in DIMENSIONS]
_PREV_CUR = re.compile(r"previous\s+rating\s*\|?\s*current\s+rating", re.I)

AUTHORITY = {"Project Performance Assessment Report": 3, "Implementation Completion Report Review": 3,
             "Implementation Completion and Results Report": 2, "Implementation Status and Results Report": 1,
             "Project Appraisal Document": 0, "Project Paper": 0}
IEG_TYPES = {"Project Performance Assessment Report", "Implementation Completion Report Review"}
ICR_TYPE = "Implementation Completion and Results Report"
ISR_TYPE = "Implementation Status and Results Report"


def score_of(value_text):
    return SCALE.get(re.sub(r"\s+", " ", value_text.strip().lower()))


def extract_ratings(text):
    """All (dimension, value name, score, matched text) found in a passage, one per dimension (the first match).
    When a label is followed by two values in adjacent table cells and the passage has a 'Previous Rating | Current
    Rating' header, the second value is the current one; otherwise the first value is used. [A] This layout comes from
    ISR templates and is checked on a printed sample in notebook 55."""
    found, seen = [], set()
    two_col = bool(_PREV_CUR.search(text or ""))
    for pat, dim in _PATTERNS:
        if dim in seen:
            continue
        m = pat.search(text or "")
        if m:
            first, second = m.groups()[-2], m.groups()[-1]
            value = second if (second and two_col) else first
            found.append({"dimension": dim, "value": NAME_OF[score_of(value)], "score": score_of(value),
                          "matched": re.sub(r"\s+", " ", m.group(0)).strip()[:200]})
            seen.add(dim)
    return found


def _d(x):
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def resolve(observations, dimension, question="outcome", as_of=None):
    """Pick the rating that answers a question, and report every conflict.

    observations: dicts with dimension, score, doc_type, doc_date, chunk_id (and optionally value, matched).
    question: "outcome" (final result: IEG > ICR > latest ISR) or "state" (latest ISR on/before as_of).
    Returns {"chosen", "rule", "others", "flags"}; chosen is None when nothing is available as of the date.
    """
    obs = [o for o in observations if o["dimension"] == dimension and o.get("score")]
    if as_of is not None:
        obs = [o for o in obs if _d(o["doc_date"]) <= _d(as_of)]
    obs.sort(key=lambda o: _d(o["doc_date"]))
    if not obs:
        return {"chosen": None, "rule": "no rating available as of the date", "others": [], "flags": []}
    if question == "state":
        isrs = [o for o in obs if o["doc_type"] == ISR_TYPE]
        chosen = isrs[-1] if isrs else obs[-1]
        rule = "latest ISR on or before the date" if isrs else "no ISR: latest available rating"
    else:
        chosen = max(obs, key=lambda o: (AUTHORITY.get(o["doc_type"], 0), _d(o["doc_date"])))
        rule = "most authoritative source: IEG review > ICR > latest ISR"
    return {"chosen": chosen, "rule": rule, "others": [o for o in obs if o is not chosen], "flags": flags(obs)}


def flags(obs):
    """Conflict flags over one dimension's observations (sorted by date)."""
    out = []
    isrs = [o for o in obs if o["doc_type"] == ISR_TYPE]
    icrs = [o for o in obs if o["doc_type"] == ICR_TYPE]
    iegs = [o for o in obs if o["doc_type"] in IEG_TYPES]
    if icrs:
        icr = icrs[-1]
        before = [o for o in isrs if _d(o["doc_date"]) <= _d(icr["doc_date"])]
        if before and abs(before[-1]["score"] - icr["score"]) >= 2:
            out.append({"flag": "isr_icr_disconnect",
                        "detail": f"last ISR {NAME_OF[before[-1]['score']]} ({before[-1]['doc_date']}) vs ICR "
                                  f"{NAME_OF[icr['score']]} ({icr['doc_date']})",
                        "chunks": [before[-1].get("chunk_id"), icr.get("chunk_id")]})
        if iegs and iegs[-1]["score"] < icr["score"]:
            out.append({"flag": "ieg_downgrade",
                        "detail": f"ICR {NAME_OF[icr['score']]} vs IEG {NAME_OF[iegs[-1]['score']]}",
                        "chunks": [icr.get("chunk_id"), iegs[-1].get("chunk_id")]})
    for a, b in zip(isrs, isrs[1:]):
        if abs(a["score"] - b["score"]) >= 2:
            out.append({"flag": "isr_volatility",
                        "detail": f"{NAME_OF[a['score']]} ({a['doc_date']}) -> {NAME_OF[b['score']]} ({b['doc_date']})",
                        "chunks": [a.get("chunk_id"), b.get("chunk_id")]})
    return out
