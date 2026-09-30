"""Transliteration normaliser, IPC/BNS extractor, mixed-script NER and transliteration-aware resolve/check."""

import asyncio
from unittest.mock import MagicMock

import pytest

import indic_ner
import indic_text
import text_extraction
from indic_text import canonical_name, detect_script, extract_legal_sections, name_aliases

pytestmark = pytest.mark.skipif(indic_text.sanscript is None, reason="indic-transliteration not installed")


@pytest.mark.parametrize("variant", ["Ramesh", "Rameś", "रमेश", "RAMESH", "ramesh ", "Rameshh"])
def test_ramesh_variants_share_one_canonical_form(variant):
    assert canonical_name(variant) == "ramesh"


def test_full_names_and_honorifics():
    assert canonical_name("Shri Ramesh Kumar") == canonical_name("रमेश कुमार") == "ramesh kumar"
    assert canonical_name("शीतल") == canonical_name("Sheetal")
    assert canonical_name("") == "" and canonical_name("  ...  ") == ""


def test_aliases_keep_original_script_only_when_different():
    assert name_aliases("रमेश") == ["रमेश"]
    assert name_aliases("ramesh") == []


def test_detect_script():
    assert detect_script("रमेश") == "devanagari"
    assert detect_script("Ramesh") == "latin"
    assert detect_script("आरोपी Ramesh Kumar") == "mixed"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("booked u/s 376, 354D IPC and 34 IPC", [("376", "IPC", "SEXUAL_OFFENCE"), ("354D", "IPC", "STALKING")]),
        ("under Section 64(2) BNS r/w 3(5)", [("64(2)", "BNS", "SEXUAL_OFFENCE")]),
        ("Sections 78 and 75 of Bharatiya Nyaya Sanhita", [("78", "BNS", "STALKING"), ("75", "BNS", "HARASSMENT")]),
        ("धारा ३७६ भा.द.वि. के तहत", [("376", "IPC", "SEXUAL_OFFENCE")]),
        ("Sec. 376AB IPC, 354 IPC", [("376AB", "IPC", "SEXUAL_OFFENCE"), ("354", "IPC", "ASSAULT")]),
        ("Section 498A of the Indian Penal Code", [("498A", "IPC", "DOMESTIC_VIOLENCE")]),
        ("IPC 326A", [("326A", "IPC", "ACID_ATTACK")]),
        ("under section 420", [("420", "UNKNOWN", "UNCLASSIFIED")]),
        ("Section 354-D IPC and 376-AB IPC", [("354D", "IPC", "STALKING"), ("376AB", "IPC", "SEXUAL_OFFENCE")]),
        ("Sections 78 and 75 of BNS", [("78", "BNS", "STALKING"), ("75", "BNS", "HARASSMENT")]),
    ],
)
def test_legal_section_extraction(text, expected):
    got = [(s["section"], s["act"], s["offense_category"]) for s in extract_legal_sections(text)]
    assert got == expected


def test_legal_sections_ignore_phone_numbers_and_flag_womens_safety():
    assert extract_legal_sections("call 9876543210 now") == []
    hit = extract_legal_sections("IPC 354D")[0]
    assert hit["womens_safety"] is True and 0 < hit["confidence"] <= 1 and hit["evidence"]


MIXED = "आरोपी रमेश कुमार ने Suspect: Ramesh Kumar 9876543210 और FIR-12/2026 का उल्लेख किया।"


def _fake_model(text):
    start = text.index("रमेश कुमार")
    return [{"entity_group": "PER", "score": 0.97, "word": "रमेश कुमार", "start": start, "end": start + 10}]


def test_mixed_script_text_dedupes_person_across_scripts(monkeypatch):
    monkeypatch.setattr(text_extraction, "active_provider", lambda: None)
    monkeypatch.setattr(indic_ner, "ENABLE_INDIC_NER", True)
    monkeypatch.setattr(indic_ner, "_get_pipeline", lambda: _fake_model)
    result = text_extraction.extract_candidates(MIXED)
    assert result.extraction_method == "indic_ner"
    persons = [e for e in result.entities if e.type == "person"]
    assert len(persons) == 1                                   # रमेश कुमार and Ramesh Kumar are one person
    assert persons[0].canonical == "ramesh kumar"
    assert persons[0].aliases == ["रमेश कुमार"]
    assert any(e.type == "fir" for e in result.entities)       # Latin-script parts still extracted


def test_canonical_stamped_on_regex_fallback(monkeypatch):
    monkeypatch.setattr(text_extraction, "active_provider", lambda: None)
    result = text_extraction.extract_candidates("Accused: Ramesh Kumar used 9876543210.")
    person = next(e for e in result.entities if e.type == "person")
    assert person.canonical == "ramesh kumar" and person.aliases == []


def test_resolve_check_matches_across_scripts(monkeypatch):
    import platform_api as pa
    from rapidfuzz import fuzz

    # rapidfuzz is stubbed in unit tests; use a real-enough ratio.
    import difflib
    monkeypatch.setattr(fuzz, "ratio", lambda a, b: 100 * difflib.SequenceMatcher(None, a, b).ratio())
    session = MagicMock()
    session.run.return_value = [{"name": "रमेश"}, {"name": "Suresh Patel"}]
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(pa, "_neo4j", driver)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    out = asyncio.run(pa.resolve_check("Ramesh", current_user={"full_name": "t", "role": "admin", "jurisdiction": "National"}))
    assert out[0]["candidate"] == "रमेश"
    assert out[0]["match_type"] == "transliteration_match" and out[0]["similarity"] == 1.0
    assert all(c["candidate"] != "Suresh Patel" or c["similarity"] < 0.92 for c in out)
