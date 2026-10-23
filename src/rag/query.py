"""Question understanding for "Ask the evidence" (2026-10-23, after app testing). Unit-tested.

Plain-language summary: three fixes for problems found by asking real questions in the app.
  1. Instructions are not search terms. "Tell me in detail", "explain", "summarise" changed WHAT was retrieved, so the
     answer changed completely. They are now removed from the search query and passed to the answer step as a style
     request instead.
  2. Project names become project IDs. "the Yunnan highway project" is matched against the names of the road projects
     (distinctive words such as "Yunnan" plus supporting words such as "highway") and the matched project ID filters
     the search, exactly like a typed "P132621".
  3. Rating questions get the rating timeline as structured facts. The ratings extracted from ISRs, ICRs and IEG reviews
     (with their source passages), the final-outcome rule and any conflict flags are given to the model, so it does not
     have to piece a timeline together from whichever passages happen to be retrieved, and cannot confuse scales
     (e.g. IEG "efficacy: High" is not an ISR implementation-progress rating).
"""
import re
import unicodedata

from src.conflict import ratings as cr

# ---------- 1. instructions vs search terms ----------
_INSTRUCTION = re.compile(
    r"\b(?:"
    r"(?:please\s+)?(?:can|could|would)\s+you\s+(?:please\s+)?"
    r"|please"
    r"|tell\s+me(?:\s+(?:more|everything|all))?(?:\s+about)?"
    r"|(?:in|with)\s+(?:great|full|more|much|some)?\s*detail(?:s)?"
    r"|in\s+depth|step\s+by\s+step|with\s+examples|in\s+short|briefly|in\s+brief|in\s+(?:a\s+)?few\s+words"
    r"|(?:give\s+(?:me\s+)?)?(?:a\s+)?(?:short\s+|brief\s+|detailed\s+)?(?:summary|overview)\s+of"
    r"|summari[sz]e|explain(?:\s+to\s+me)?|elaborate(?:\s+on)?|be\s+(?:detailed|brief|concise)"
    r"|detailed|thoroughly|concisely"
    r")\b[\s,:;.!-]*", re.I)


def split_instructions(question):
    """(search_query, style): the question without answer-style instructions, and the instructions removed (joined),
    which go to the answer step. A question made only of instructions keeps its original text as the query."""
    q = question or ""
    found = [m.group(0).strip(" ,:;.!-") for m in _INSTRUCTION.finditer(q)]
    stripped = re.sub(r"\s+([?.!,;:])", r"\1", re.sub(r"\s{2,}", " ", _INSTRUCTION.sub(" ", q))).strip(" ,:;.!-")
    stripped = re.sub(r"^(?:and|about|me|on)\s+", "", stripped, flags=re.I).strip()
    if len(re.findall(r"\w+", stripped)) < 3:
        return q.strip(), ""
    if stripped and stripped[0].islower():
        stripped = stripped[0].upper() + stripped[1:]
    if not stripped.endswith("?") and q.rstrip().endswith("?"):
        stripped += "?"
    return stripped, "; ".join(f for f in found if f)


# ---------- 2. project names -> project IDs ----------
_GENERIC = set("""project projects program programme the of and for in to a an with phase additional financing af
national state provincial province regional sector road roads highway highways rural urban inter transport
transportation improvement improvements development connectivity network networks corridor corridors asset management
infrastructure investment support second third ii iii iv sustainable integrated access cn ln""".split())
_STOP = set("the a an of and or for to in on at by with from what which who how did does was were is are be been this "
            "that these those about over time earlier final rating rated ratings progress report reports differ".split())


def _fold(text):
    d = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in d if not unicodedata.combining(ch)).lower()


def _words(text):
    return re.findall(r"[a-z][a-z0-9]{2,}", _fold(text))


def resolve_project_names(question, projects, max_hits=2):
    """Project IDs whose NAME the question refers to. projects: dicts with project_id, project_name, in_slice.
    A project matches when the question contains at least one DISTINCTIVE word of its name (not generic, in at most 3
    project names); matches are ranked by how many of the name's words appear (distinctive words count double), demo
    projects first on ties. Ambiguous results (more than max_hits equally good) return [] rather than a guess."""
    qw = set(_words(question)) - _STOP
    if not qw:
        return []
    names = {p["project_id"]: set(_words(p.get("project_name"))) for p in projects}
    df = {}
    for ws in names.values():
        for w in ws:
            df[w] = df.get(w, 0) + 1
    scored = []
    for p in projects:
        ws = names[p["project_id"]]
        distinct = {w for w in ws if w not in _GENERIC and df.get(w, 0) <= 3}
        hit_d = distinct & qw
        if not hit_d:
            continue
        hit_g = (ws - distinct) & qw
        scored.append((2 * len(hit_d) + len(hit_g), bool(p.get("in_slice")), p["project_id"]))
    if not scored:
        return []
    scored.sort(reverse=True)
    best = [s for s in scored if s[0] == scored[0][0]]
    if len(best) > max_hits:
        slice_best = [s for s in best if s[1]]
        best = slice_best if 0 < len(slice_best) <= max_hits else []
    return [s[2] for s in best[:max_hits]]


# ---------- 3. rating questions -> rating timeline facts ----------
_RATING_Q = re.compile(r"\b(rat(ed|ing|ings)|satisfactory|unsatisfactory|downgrad\w*|upgrad\w*|outcome|progress|"
                       r"performance|conflict\w*|disconnect|volatil\w*)\b", re.I)
_TREND_Q = re.compile(r"\b(over\s+time|trend|timeline|history|earlier|previous|progress\s+reports?|isrs?|change[sd]?|"
                      r"evolv\w*|differ\w*|compare[sd]?|before|during)\b", re.I)
DIMENSION_LABEL = {
    "implementation": "implementation progress (ISR six-point scale)",
    "outcome": "development outcome / progress towards the PDO (six-point scale)",
    "bank_performance": "Bank performance", "procurement": "procurement", "financial_management": "financial management",
    "project_management": "project management", "m_and_e": "monitoring and evaluation",
}


def is_rating_question(question):
    return bool(_RATING_Q.search(question or ""))


def is_trend_question(question):
    return bool(_TREND_Q.search(question or ""))


def rating_facts(rows):
    """Structured facts from extracted ratings (rows of gold.ratings: project_id, dimension, value, score, doc_type,
    doc_date, chunk_id): one fact per rating in date order, then per project and dimension the final-outcome rule's
    choice and any conflict flags (src/conflict/ratings.py). Every fact names its source passage."""
    facts = []
    rows = [r for r in rows if r.get("value")]
    rows.sort(key=lambda r: (r["project_id"], r["dimension"], str(r["doc_date"])))
    for r in rows:
        facts.append({"text": f"{r['project_id']} · {r['doc_type']} dated {str(r['doc_date'])[:10]}: "
                              f"{DIMENSION_LABEL.get(r['dimension'], r['dimension'])} rated {r['value']} "
                              f"(source passage {r['chunk_id']})",
                      "kind": "rating", "chunk_id": r["chunk_id"], "project_id": r["project_id"]})
    by = {}
    for r in rows:
        if r.get("score") is None:
            r = dict(r, score=cr.score_of(r["value"]))
        if r.get("score"):
            by.setdefault((r["project_id"], r["dimension"]), []).append(r)
    for (pid, dim), obs in sorted(by.items()):
        res = cr.resolve(obs, dim, question="outcome")
        if res["chosen"]:
            c = res["chosen"]
            facts.append({"text": f"{pid} · final {DIMENSION_LABEL.get(dim, dim)}: {c['value']} from the "
                                  f"{c['doc_type']} dated {str(c['doc_date'])[:10]} (rule: {res['rule']})",
                          "kind": "rating_rule", "project_id": pid})
        for f in res["flags"]:
            facts.append({"text": f"{pid} · conflict flag {f['flag']} on {DIMENSION_LABEL.get(dim, dim)}: {f['detail']}",
                          "kind": "rating_flag", "project_id": pid})
    if rows:
        facts.append({"text": "Rating scales differ: ISR implementation progress and outcome ratings use the six-point "
                              "scale (Highly Satisfactory ... Highly Unsatisfactory); ICR / IEG 'efficacy' of an "
                              "objective uses High / Substantial / Modest / Negligible and is not an ISR rating.",
                      "kind": "rating_note"})
    return facts
