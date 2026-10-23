"""French retrieval helpers (2026-10-23, one final attempt; see docs/07 §4b). Unit-tested.

Plain-language summary: the embedding model and the question are English, but 196 passages (Mali notices and plans,
one ICR annex) are French. Two additions:
  1. English keyword matching for translated passages: a keyword index over ALL passages that uses the English
     translation for those 196. Only translated passages are taken from it, and each is placed at the rank it earned
     against the whole corpus, so it competes fairly with the Vector Search hybrid hits. Shown / cited text stays the
     original French.
  2. A French version of the question (1 LLM call) when the question concerns a French-speaking country, searched in
     the same scopes and merged.
"""
import re
import unicodedata

# French-speaking borrowers (World Bank country codes; French is an official language of administration). Only Mali
# is in the demo slice; the others make the rule usable beyond it.
FRANCOPHONE_ISO3 = {"MLI", "SEN", "CIV", "BFA", "NER", "GIN", "TCD", "CMR", "BEN", "TGO", "COD", "COG", "GAB", "CAF",
                    "MDG", "HTI", "MRT", "BDI", "DJI", "COM"}
# Names that mark a question as being about a French-speaking country: country names plus Mali's regions and capital
# (the slice's French-speaking country). Accents are ignored when matching.
_PLACES = ["mali", "senegal", "cote d'ivoire", "ivory coast", "burkina faso", "niger", "guinea", "guinee", "chad",
           "tchad", "cameroon", "cameroun", "benin", "togo", "congo", "gabon", "central african republic",
           "madagascar", "haiti", "mauritania", "burundi", "djibouti", "comoros",
           "bamako", "kayes", "koulikoro", "sikasso", "segou", "mopti", "tombouctou", "timbuktu", "gao", "kidal"]
_PLACE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _PLACES) + r")\b")
_NOT = re.compile(r"\b(nigeria|guinea-bissau|equatorial guinea|papua new guinea)\b")

TRANSLATE_QUESTION_PROMPT = (
    "Translate the user's question into French, as it would be phrased in a World Bank procurement document. Keep "
    "codes, reference numbers, project IDs, amounts and place names unchanged. Reply with the French question only.")


def _fold(text):
    d = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in d if not unicodedata.combining(ch)).lower()


def french_context(question, pkg=None):
    """True when the package's country is French-speaking or the question names a French-speaking country or a
    Mali region (the demo slice's French-speaking country)."""
    iso = (pkg or {}).get("country_iso3")
    if iso and iso in FRANCOPHONE_ISO3:
        return True
    q = _NOT.sub(" ", _fold(question))
    return bool(_PLACE_RE.search(q))


def translate_messages(question):
    return [{"role": "system", "content": TRANSLATE_QUESTION_PROMPT}, {"role": "user", "content": question}]


def clean_translation(reply, original):
    """The model's French question, or None when the reply is empty or obviously not a single question."""
    t = (reply or "").strip().strip('"').strip()
    if not t or len(t) > 4 * max(len(original), 40) or "\n\n" in t:
        return None
    return t


def rank_aligned(vector_hits, keyword_hits, name="translated_keyword"):
    """Add keyword hits on TRANSLATED passages to a Vector Search hit list. keyword_hits are ranked over the whole
    corpus (best first) and flag translated passages with "translated"; a translated passage at keyword rank r gets
    just under the score of the r-th vector hit (or just below the last one), so it ranks where its keyword evidence
    puts it, never above the vector hit it is aligned with. Passages already present only get
    the source added. Keyword-only hits carry no vector_score: they never count for the retrieval gate."""
    out = [dict(h) for h in vector_hits]
    present = {h["chunk_id"]: h for h in out}
    scores = sorted((h.get("score") or 0.0 for h in out), reverse=True)
    floor = (scores[-1] * 0.99) if scores else 0.0
    for r, h in enumerate(keyword_hits, start=1):
        cid = h["chunk_id"]
        if not h.get("translated"):
            continue
        if cid in present:
            present[cid]["found_by_keyword"] = name
            continue
        s = round(scores[r - 1] * 0.999, 6) if r <= len(scores) else floor
        new = dict(h, score=s, vector_score=None, found_by_keyword=name)
        out.append(new)
        present[cid] = new
    return out
