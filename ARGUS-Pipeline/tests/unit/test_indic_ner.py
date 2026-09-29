"""Hindi NER fallback: gating and label mapping, using a fake model (the real one is a 400 MB download)."""

import indic_ner
import text_extraction

HINDI = "आरोपी रमेश कुमार ने दिल्ली में ९८७६५४३२१० पर संपर्क किया। कंपनी: शक्ति ट्रेडर्स।"


def fake_model(text):
    def span(word, group, score):
        start = text.index(word)
        return {"entity_group": group, "score": score, "word": word, "start": start, "end": start + len(word)}
    return [span("रमेश कुमार", "PER", 0.98), span("दिल्ली", "LOC", 0.91), span("शक्ति ट्रेडर्स", "ORG", 0.72), span("९८७६५४३२१०", "MISC", 0.9), span("आरोपी", "PER", 0.2)]


def _setup(monkeypatch, enabled=True, provider=None):
    monkeypatch.setattr(text_extraction, "active_provider", lambda: provider)
    monkeypatch.setattr(indic_ner, "ENABLE_INDIC_NER", enabled)
    monkeypatch.setattr(indic_ner, "_get_pipeline", lambda: fake_model)


def test_labels_are_mapped_and_low_scores_dropped(monkeypatch):
    _setup(monkeypatch)
    result = text_extraction.extract_candidates(HINDI)
    assert result.extraction_method == "indic_ner"
    got = {(e.type, e.value) for e in result.entities}
    assert ("person", "रमेश कुमार") in got and ("location", "दिल्ली") in got and ("organization", "शक्ति ट्रेडर्स") in got
    assert ("person", "आरोपी") not in got                 # score 0.2 is below INDIC_NER_MIN_SCORE
    assert all(e.type != "MISC" for e in result.entities)  # unmapped labels are ignored
    assert ("phone", "9876543210") in got                  # Devanagari digits are normalised


def test_disabled_by_default_falls_back_to_regex(monkeypatch):
    _setup(monkeypatch, enabled=False)
    assert text_extraction.extract_candidates(HINDI).extraction_method == "regex_fallback"


def test_latin_text_never_uses_the_indic_model(monkeypatch):
    _setup(monkeypatch)
    assert text_extraction.extract_candidates("Suspect Ramesh Kumar used 9876543210 near Central Market.").extraction_method == "regex_fallback"


def test_model_unavailable_degrades_to_regex(monkeypatch):
    _setup(monkeypatch)
    def boom():
        raise RuntimeError("transformers not installed")
    monkeypatch.setattr(indic_ner, "_get_pipeline", boom)
    assert text_extraction.extract_candidates(HINDI).extraction_method == "regex_fallback"


def test_llm_still_wins_when_it_works(monkeypatch):
    _setup(monkeypatch, provider=("groq", "u", "k", "m"))
    sentinel = text_extraction.ExtractionResult(entities=[], relationships=[], extraction_method="groq_zero_shot")
    monkeypatch.setattr(text_extraction, "_llm_extract", lambda t: sentinel)
    assert text_extraction.extract_candidates(HINDI) is sentinel


def test_devanagari_detection():
    assert indic_ner.has_devanagari("नमस्ते world") and not indic_ner.has_devanagari("plain ascii")
