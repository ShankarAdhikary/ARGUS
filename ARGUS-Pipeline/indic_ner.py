"""Hindi / Indic named-entity fallback for text extraction.

Used only when the LLM provider is unavailable AND the text contains Devanagari, and only when
ENABLE_INDIC_NER=true (the model is ~400 MB and needs the optional `requirements-indic.txt`).
"""

from __future__ import annotations

import re
import threading
from typing import Optional

from config import ENABLE_INDIC_NER, INDIC_NER_MIN_SCORE, INDIC_NER_MODEL
from indic_text import canonical_name, detect_script, normalise_text_digits
from text_extraction import ExtractedEntity, ExtractedRelationship, ExtractionResult, _regex_fallback

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# IndicNER labels -> ARGUS entity types. Anything else (dates, numbers, ...) is ignored.
_LABELS = {"PER": "person", "PERSON": "person", "ORG": "organization", "LOC": "location", "LOCATION": "location", "GPE": "location"}

_pipeline = None
_lock = threading.Lock()


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def is_enabled() -> bool:
    return ENABLE_INDIC_NER


def _get_pipeline():
    global _pipeline
    with _lock:
        if _pipeline is None:
            try:
                from transformers import pipeline
            except ImportError as exc:  # optional dependency
                raise RuntimeError(
                    "Indic NER needs transformers and torch; build the image with INSTALL_INDIC_NER=true"
                ) from exc
            try:
                _pipeline = pipeline("ner", model=INDIC_NER_MODEL, aggregation_strategy="simple")
            except Exception as exc:  # download / load failure
                raise RuntimeError(f"Could not load Indic NER model {INDIC_NER_MODEL!r}: {exc}") from exc
        return _pipeline


def _snippet(text: str, start: int, end: int) -> str:
    left = max(text.rfind("।", 0, start), text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right_candidates = [i for i in (text.find("।", end), text.find(".", end), text.find("\n", end)) if i != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return (text[left:right].strip() or text[start:end])[:300]


def extract_indic(text: str) -> ExtractionResult:
    """Run IndicNER and map its PER / ORG / LOC spans onto ARGUS entities. Raises RuntimeError if unavailable.

    Devanagari, Latin-transliterated and mixed-script text are all handled: the model reads the Devanagari spans, and
    the Latin-script parts of a mixed FIR (names, plates, FIR numbers) come from the deterministic extractor. Entities
    are de-duplicated on their canonical form, so "रमेश" and "Ramesh" in one document are one person.
    """
    results = _get_pipeline()(text)
    entities: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()

    def add(entity_type: str, value: str, confidence: float, evidence: str) -> None:
        value = value.strip()
        key = (entity_type, canonical_name(value) if entity_type in {"person", "location", "organization"} else value)
        if value and key not in seen:
            seen.add(key)
            entities.append(ExtractedEntity(type=entity_type, value=value[:500], confidence=round(min(max(confidence, 0.0), 1.0), 2), evidence=evidence[:1000] or value))

    for item in results:
        entity_type = _LABELS.get(str(item.get("entity_group") or item.get("entity", "")).replace("B-", "").replace("I-", "").upper())
        score = float(item.get("score", 0))
        if not entity_type or score < INDIC_NER_MIN_SCORE:
            continue
        start, end = item.get("start"), item.get("end")
        value = text[start:end] if isinstance(start, int) and isinstance(end, int) else str(item.get("word", ""))
        add(entity_type, value, score, _snippet(text, start, end) if isinstance(start, int) and isinstance(end, int) else value)

    # Phone numbers are language-independent; the NER model does not label them. Devanagari digits are normalised.
    for match in re.finditer(r"(?<!\d)(?:\+91[-\s]?)?[6-9]\d{9}(?!\d)", normalise_text_digits(text)):
        add("phone", match.group(0), 0.99, match.group(0))

    if detect_script(text) == "mixed":
        for extra in _regex_fallback(text).entities:
            if extra.type != "phone":
                add(extra.type, extra.value, extra.confidence, extra.evidence)

    persons = [e for e in entities if e.type == "person"]
    phones = [e for e in entities if e.type == "phone"]
    relationships = []
    if persons and phones:
        relationships.append(ExtractedRelationship(source=persons[0].value, target=phones[0].value, type="USES_PHONE", confidence=0.5, evidence=persons[0].evidence))
    return ExtractionResult(entities=entities, relationships=relationships, extraction_method="indic_ner")
