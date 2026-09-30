"""Phase 6: voice-to-FIR endpoint, with Whisper faked (the real model is a ~500 MB download)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import evidence
import indic_text
import platform_api as pa
import voice
from tests.unit.test_platform_critical_path import _auth_headers

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
TRANSCRIPT = "Accused Ramesh Kumar called from 9876543210 near Saket Police Station on 12/03/2026. Booked under Section 354D IPC."


def test_sniff_audio_recognises_accepted_containers():
    assert voice.sniff_audio(WAV) == "wav"
    assert voice.sniff_audio(b"ID3\x03" + b"\x00" * 12) == "mp3"
    assert voice.sniff_audio(b"\xff\xfb\x90\x00" + b"\x00" * 12) == "mp3"
    assert voice.sniff_audio(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8) == "m4a"
    assert voice.sniff_audio(b"\x1a\x45\xdf\xa3" + b"\x00" * 12) == "webm"
    assert voice.sniff_audio(b"MZ\x90\x00 not audio at all") is None and voice.sniff_audio(b"") is None


def test_find_date_formats():
    assert voice._find_date("on 12/03/2026 at night") == "2026-03-12"
    assert voice._find_date("2026-9-8") == "2026-09-08" and voice._find_date("no date") is None


@pytest.fixture
def client(monkeypatch):
    import main
    monkeypatch.setattr(main, "_put_object", MagicMock())
    monkeypatch.setattr(evidence, "record_upload", lambda *a, **k: {"file_sha256": "abc", "row_hash": "def", "ledger_id": "L"})
    monkeypatch.setattr(main, "log_action", MagicMock())
    monkeypatch.setattr(voice, "transcribe", lambda audio, suffix, language: {"text": TRANSCRIPT, "language": "en", "segments": []})
    monkeypatch.setattr("text_extraction.active_provider", lambda: None)
    return TestClient(main.app)


def post(client, name="c.wav", data=WAV, role="investigator", **form):
    return client.post("/api/v1/ingest/voice", files={"file": (name, data, "audio/wav")}, data=form, headers=_auth_headers(role))


@pytest.mark.skipif(indic_text.sanscript is None, reason="indic-transliteration not installed")
def test_voice_returns_transcript_entities_and_suggested_fields(client):
    r = post(client)
    body = r.json()
    assert r.status_code == 200 and body["transcript"] == TRANSCRIPT
    kinds = {e["type"] for e in body["entities"]}
    assert {"person", "phone", "legal_section"} <= kinds
    f = body["suggested_fir_fields"]
    assert f["fields"]["accused"] == "Ramesh Kumar" and f["fields"]["mobile"] == "9876543210"
    assert f["fields"]["sections"] == ["IPC 354D"] and f["fields"]["date"] == "2026-03-12"
    assert f["fields"]["station"] == "Saket Police Station" and "verify before use" in f["label"]
    assert body["evidence"]["file_sha256"] == "abc"


def test_voice_rejects_bad_extension_content_and_role(client):
    assert post(client, name="x.txt").status_code == 415
    assert post(client, data=b"this is plainly not audio data").status_code == 415
    assert post(client, role="analyst").status_code == 403


def test_voice_silence_and_disabled(client, monkeypatch):
    monkeypatch.setattr(voice, "transcribe", lambda audio, suffix, language: {"text": "", "language": "en", "segments": []})
    assert post(client).status_code == 422
    monkeypatch.setattr(voice, "is_enabled", lambda: False)
    assert post(client).status_code == 503


def test_voice_model_unavailable_is_503(client, monkeypatch):
    def boom(*a):
        raise RuntimeError("no weights")
    monkeypatch.setattr(voice, "transcribe", boom)
    assert post(client).status_code == 503
