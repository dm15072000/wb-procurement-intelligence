"""Language of a passage: 'en', 'fr' or 'other' (unit-tested). No extra library.

Plain-language summary: the embedding model (databricks-gte-large-en) is trained on English, so a French passage is
placed poorly in the vector space and an English question rarely finds it. We count very common English and French
function words ("the", "of", "and" vs "le", "des", "et"); whichever is clearly ahead wins. Text written mostly in a
non-Latin script (e.g. Chinese) is 'other'. Short or mixed text with no clear winner falls back to 'en' only when
English words are present, else 'other'.
"""
import re

EN = set("the of and to in is for that with on by this are be as at from was were which or an it has have will "
         "not their its project projects contract contracts".split())
FR = set("le la les des du de et en un une pour par dans sur au aux est sont qui que avec ce cette ces ont été "
         "projet marché marchés travaux".split())
MIN_WORDS = 8
MARGIN = 1.5


def detect(text):
    t = text or ""
    letters = [ch for ch in t if ch.isalpha()]
    if letters and sum(ord(ch) > 0x24F for ch in letters) / len(letters) > 0.3:   # mostly non-Latin script
        return "other"
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", t.lower())
    words = [w.split("'")[-1] for w in words]            # "l'appel" -> "appel", "d'offres" -> "offres"
    en = sum(w in EN for w in words)
    fr = sum(w in FR for w in words)
    # A long passage with almost no English or French function words is a table, a list of names or another language
    # (an Albanian passage and two numeric tables were labelled French by the first version, 2026-10-22).
    if len(words) >= 40 and en + fr < 0.08 * len(words):
        return "other"
    if en + fr < MIN_WORDS:
        return "en" if en > fr else ("fr" if fr > en else "other")
    if fr >= MARGIN * en:
        return "fr"
    if en >= MARGIN * fr:
        return "en"
    return "other"                                       # genuinely mixed (bilingual headers and body)
