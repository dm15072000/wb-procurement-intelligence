"""French retrieval helpers (src/rag/multilingual.py) and their use in pipeline.retrieve."""
from src.rag import multilingual as ml
from src.rag import pipeline as pl


def test_french_context_from_country_or_place_names():
    assert ml.french_context("How many lots did the works in the Sikasso region have?")
    assert ml.french_context("What did the Mali project's ICR recommend?")
    assert ml.french_context("anything", {"country_iso3": "MLI"})
    assert ml.french_context("Travaux à Ségou")                       # accents ignored
    assert not ml.french_context("What lessons did rural road works in Nigeria report?")
    assert not ml.french_context("Guinea-Bissau road contracts")
    assert not ml.french_context("Which contractor built the Sichuan roads?", {"country_iso3": "CHN"})


def test_clean_translation():
    assert ml.clean_translation(' "Combien de lots ?" ', "How many lots?") == "Combien de lots ?"
    assert ml.clean_translation("", "q") is None and ml.clean_translation(None, "q") is None
    assert ml.clean_translation("a\n\nb", "How many lots?") is None


def test_rank_aligned_places_translated_hits_at_keyword_rank_and_never_counts_for_gate():
    vh = [{"chunk_id": f"v{i}", "score": s, "vector_score": 0.7} for i, s in enumerate([0.9, 0.8, 0.7, 0.6])]
    kh = [{"chunk_id": "en1", "kw_score": 1.0},                        # rank 1, not translated: ignored
          {"chunk_id": "fr1", "kw_score": 0.9, "translated": True},    # rank 2 -> just under the 2nd vector hit
          {"chunk_id": "v3", "kw_score": 0.5, "translated": True},     # already present: only marked
          {"chunk_id": "fr2", "kw_score": 0.4, "translated": True}]    # rank 4 -> just under the 4th
    out = {h["chunk_id"]: h for h in ml.rank_aligned(vh, kh)}
    assert "en1" not in out
    assert 0.7 < out["fr1"]["score"] < 0.8 and out["fr1"]["vector_score"] is None
    assert out["v3"]["score"] == 0.6 and out["v3"]["found_by_keyword"] == "translated_keyword"
    assert out["fr2"]["score"] < 0.6
    tail = ml.rank_aligned(vh[:1], [{"chunk_id": "a", "translated": True}, {"chunk_id": "b", "translated": True}])
    assert [h["chunk_id"] for h in tail] == ["v0", "a", "b"] and tail[2]["score"] < 0.9


def _hit(cid, score, **kw):
    return dict({"chunk_id": cid, "doc_id": cid.split("-")[0], "doc_type": "Procurement Notice", "score": score,
                 "vector_score": score, "text": "texte", "section_path": ""}, **kw)


def test_retrieve_adds_french_query_pass_only_for_french_context():
    calls = {"translate": 0, "queries": []}

    def search(vec, filters, k, text=None):
        calls["queries"].append(text)
        return [_hit("fr-1", 0.8)] if text and text.startswith("Combien") else [_hit("en-1", 0.7)]

    def translate(q):
        calls["translate"] += 1
        return "Combien de lots à Sikasso ?"

    tools = pl.Tools(embed=lambda t: [1.0], search=search, llm=lambda m: "", translate_query=translate)
    info = {}
    hits = pl.retrieve("How many lots in Sikasso?", {"as_of": "2026-01-01"}, tools, info=info)
    assert calls["translate"] == 1 and info["french_query"].startswith("Combien")
    assert {h["chunk_id"] for h in hits} == {"en-1", "fr-1"}
    assert any(n.endswith("_fr") for h in hits for n in h["found_by"])
    calls.update(translate=0)
    hits = pl.retrieve("Rural roads in Sichuan?", {"as_of": "2026-01-01"}, tools, info={})
    assert calls["translate"] == 0 and {h["chunk_id"] for h in hits} == {"en-1"}


def test_retrieve_uses_translated_keyword_hits_for_english_queries_only():
    seen = []

    def tkw(query, filters, k):
        seen.append(query)
        return [{"chunk_id": "fr-9", "kw_score": 1.0, "translated": True, "doc_id": "fr", "doc_type": "Procurement Plan",
                 "text": "texte original", "section_path": ""}]

    tools = pl.Tools(embed=lambda t: [1.0], search=lambda v, f, k, text=None: [_hit("en-1", 0.7)],
                     llm=lambda m: "", translated_keyword_search=tkw,
                     translate_query=lambda q: "Valeur estimée des six lots au Mali ?")
    hits = pl.retrieve("Estimated value of the six lots in Mali?", {"as_of": "2026-01-01"}, tools, info={})
    assert "fr-9" in {h["chunk_id"] for h in hits}
    assert seen and all(not q.startswith("Valeur") for q in seen)       # never called with the French query
    fr9 = next(h for h in hits if h["chunk_id"] == "fr-9")
    assert fr9["text"] == "texte original" and fr9.get("vector_score") is None
