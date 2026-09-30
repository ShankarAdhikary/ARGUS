"""Validated, zero-shot extraction for unstructured investigative text.

The LLM is used only to produce *candidate* entities and relationships. Each
candidate retains the source excerpt that supports it, and Pydantic validates
the response before it can be queued for graph ingestion.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import time
from typing import Literal, Optional

from pydantic import BaseModel, Field

from config import (
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    LLM_PROVIDER,
)


EntityType = Literal["person", "phone", "location", "vehicle", "fir", "event", "organization"]


class ExtractedEntity(BaseModel):
    type: EntityType
    value: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=1000)
    # Script-independent key for entity resolution (Ramesh / रमेश / RAMESH -> "ramesh"); set for persons, places and
    # organisations. `aliases` keeps the original spelling when it differs.
    canonical: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


class ExtractedRelationship(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=500)
    type: str = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=1000)


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]
    extraction_method: Literal["groq_zero_shot", "gemini_zero_shot", "indic_ner", "regex_fallback"]


EXTRACTION_SCHEMA = {
    "name": "argus_investigative_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["person", "phone", "location", "vehicle", "fir", "event", "organization"]},
                        "value": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["type", "value", "confidence", "evidence"],
                    "additionalProperties": False,
                },
            },
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["source", "target", "type", "confidence", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["entities", "relationships"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You extract candidate intelligence from investigative text.
Only extract facts explicitly stated in the text. Never infer criminality or
invent missing values. Each entity and relationship must contain a short exact
source excerpt in `evidence`. Relationship `source` and `target` must exactly
match an entity `value`. Return only the requested JSON schema."""


def active_provider() -> Optional[tuple[str, str, str, str]]:
    """Returns (name, base_url, api_key, model) for the configured provider, or
    None when no key is available. Both providers speak the OpenAI chat
    completions shape, so only these four values differ."""
    if LLM_PROVIDER == "gemini" and GEMINI_API_KEY:
        return ("gemini", GEMINI_BASE_URL, GEMINI_API_KEY, GEMINI_MODEL)
    if LLM_PROVIDER == "groq" and GROQ_API_KEY:
        return ("groq", GROQ_BASE_URL, GROQ_API_KEY, GROQ_MODEL)
    return None


def _llm_extract(text: str) -> ExtractionResult:
    provider = active_provider()
    if provider is None:
        raise RuntimeError(f"No API key configured for LLM_PROVIDER={LLM_PROVIDER!r}")
    name, base_url, api_key, model = provider

    schema = dict(EXTRACTION_SCHEMA)
    if name == "gemini":
        # Gemini's OpenAI-compatible layer rejects the `strict` flag that Groq
        # requires, so it is sent only to the provider that accepts it.
        schema.pop("strict", None)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": schema},
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return ExtractionResult.model_validate(
                {**parsed, "extraction_method": f"{name}_zero_shot"}
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{name} extraction failed: HTTP {exc.code} {detail}") from exc
        except (KeyError, TypeError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{name} extraction failed: {exc}") from exc


def _regex_fallback(text: str) -> ExtractionResult:
    """A deterministic fallback for the demo when an LLM key is unavailable."""
    entities: list[ExtractedEntity] = []
    relationships: list[ExtractedRelationship] = []
    seen: set[tuple[str, str]] = set()

    def add_entity(entity_type: EntityType, value: str, confidence: float) -> None:
        key = (entity_type, value)
        if key not in seen:
            seen.add(key)
            entities.append(ExtractedEntity(type=entity_type, value=value, confidence=confidence, evidence=value))

    for value in re.findall(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b", text):
        add_entity("phone", value, 0.99)
    for value in re.findall(r"\bFIR(?:[-/]\d+[A-Za-z0-9/-]*)+\b|\bFIR\s+\d+(?:[/\-]\d+)*\b|\bFIR\d+[A-Za-z0-9/-]*\b", text, flags=re.IGNORECASE):
        add_entity("fir", value, 0.99)
    for value in re.findall(r"(?:Accused|Suspect|Person)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})", text):
        add_entity("person", value, 0.70)
    for value in re.findall(r"(?:at|near|in)\s+([A-Z][A-Za-z ]{2,40})(?:[,.]|\s+on\b)", text):
        add_entity("location", value.strip(), 0.55)
    # Indian registration plates, with or without the separators people type.
    for value in re.findall(r"\b[A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4}\b", text):
        add_entity("vehicle", re.sub(r"[\s-]", "", value), 0.90)
    for value in re.findall(
        r"\b((?:[A-Z][A-Za-z&.]*\s+){1,4}"
        r"(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Ltd\.?|LLP|Holdings|Enterprises|"
        r"Trading|Traders|Logistics|Consulting|Trust|Foundation|Corporation|Corp\.?))",
        text,
    ):
        add_entity("organization", value.strip(), 0.65)

    persons = [entity.value for entity in entities if entity.type == "person"]
    phones = [entity.value for entity in entities if entity.type == "phone"]
    if persons and phones:
        evidence = next(
            (
                sentence.strip()
                for sentence in re.split(r"(?<=[.!?])\s+", text)
                if persons[0] in sentence and phones[0] in sentence
            ),
            f"{persons[0]} {phones[0]}",
        )
        relationships.append(
            ExtractedRelationship(
                source=persons[0], target=phones[0], type="USES_PHONE", confidence=0.55,
                evidence=evidence,
            )
        )
    return ExtractionResult(entities=entities, relationships=relationships, extraction_method="regex_fallback")


def _add_canonical_forms(result: ExtractionResult) -> ExtractionResult:
    from indic_text import canonical_name, name_aliases

    for entity in result.entities:
        if entity.type in {"person", "location", "organization"}:
            entity.canonical = canonical_name(entity.value) or None
            entity.aliases = name_aliases(entity.value)
    return result


def extract_candidates(text: str) -> ExtractionResult:
    """Extract candidates (LLM, then Indic NER, then regex) and stamp canonical name forms on the result."""
    return _add_canonical_forms(_extract_candidates(text))


def _extract_candidates(text: str) -> ExtractionResult:
    """Use the configured LLM provider; fall back to deterministic extraction."""
    if active_provider() is not None:
        try:
            return _llm_extract(text)
        except RuntimeError as exc:
            # A demo must stay runnable even if the provider is down, but the
            # fallback used to be silent — log it so a degraded run is visible.
            print(f"[!] LLM extraction unavailable, trying fallbacks: {exc}")
    # Hindi text: the regex fallback only understands Latin script, so try the Indic NER model if it is switched on.
    from indic_ner import extract_indic, has_devanagari, is_enabled  # imported here: indic_ner imports this module

    if is_enabled() and has_devanagari(text):
        try:
            return extract_indic(text)
        except RuntimeError as exc:
            print(f"[!] Indic NER unavailable, using regex fallback: {exc}")
    return _regex_fallback(text)
