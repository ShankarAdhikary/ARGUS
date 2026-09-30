"""Voice-to-FIR: offline Whisper transcription plus FIR field suggestions from the transcript.

Whisper is an open-weight model that runs locally, so audio and transcripts never leave the deployment.
The heavy import and model load are lazy and thread-safe: the first request pays the load, later ones reuse it.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from typing import Optional

from config import ENABLE_VOICE, WHISPER_CACHE_DIR, WHISPER_MODEL

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".mp4"}  # webm/ogg/mp4: what browser MediaRecorder produces
LEAD_LABEL = "investigative lead — verify before use"
_model = None
_lock = threading.Lock()


def is_enabled() -> bool:
    return ENABLE_VOICE


def sniff_audio(data: bytes) -> Optional[str]:
    """Container type from magic bytes, or None if this does not look like audio we accept. Extension alone is not trusted."""
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[:4] == b"OggS":
        return "ogg"
    return None


def _get_model():
    global _model
    with _lock:
        if _model is None:
            try:
                import whisper
            except ImportError as exc:
                raise RuntimeError("Voice transcription needs openai-whisper; build the image with INSTALL_VOICE=true") from exc
            try:
                _model = whisper.load_model(WHISPER_MODEL, download_root=WHISPER_CACHE_DIR if os.path.isdir(WHISPER_CACHE_DIR) else None)
            except Exception as exc:  # missing weights / no network on first use
                raise RuntimeError(f"Could not load Whisper model {WHISPER_MODEL!r}: {exc}") from exc
        return _model


def transcribe(audio: bytes, suffix: str = ".wav", language: Optional[str] = None) -> dict:
    """Whisper transcription: {text, language, segments:[{start,end,text}]}. Raises RuntimeError if unavailable."""
    model = _get_model()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(audio)
        path = fh.name
    try:
        # fp16 is GPU-only; on CPU it just warns and falls back, so ask for fp32 directly.
        result = model.transcribe(path, fp16=False, language=language, task="transcribe")
    finally:
        os.unlink(path)
    return {
        "text": (result.get("text") or "").strip(),
        "language": result.get("language"),
        "segments": [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()} for s in result.get("segments", [])],
    }


_ISO_DATE = re.compile(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b")
_DMY_DATE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})\b")


def _find_date(text: str) -> Optional[str]:
    if m := _ISO_DATE.search(text):
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if m := _DMY_DATE.search(text):
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def suggest_fir_fields(transcript: str, entities: list[dict], legal_sections: list[dict], geo: Optional[dict]) -> dict:
    """Pre-fill values for the FIR form. Each suggestion carries a confidence; the officer confirms or corrects them."""
    def first(kind: str) -> Optional[dict]:
        return next((e for e in entities if e["type"] == kind), None)

    person, phone, location = first("person"), first("phone"), first("location")
    fields = {
        "accused": person["value"] if person else None,
        "mobile": re.sub(r"\D", "", phone["value"])[-10:] if phone else None,
        "location": location["value"] if location else (geo["place"] if geo else None),
        "station": geo["place"] if geo and geo["level"] == "station" else None,
        "date": _find_date(transcript),
        "sections": [f"{s['act']} {s['section']}" for s in legal_sections],
        "description": transcript,
    }
    confidence = {
        "accused": person["confidence"] if person else 0.0,
        "mobile": phone["confidence"] if phone else 0.0,
        "location": location["confidence"] if location else (geo["confidence"] if geo else 0.0),
        "sections": min((s["confidence"] for s in legal_sections), default=0.0),
    }
    return {"fields": fields, "confidence": confidence, "label": LEAD_LABEL}
