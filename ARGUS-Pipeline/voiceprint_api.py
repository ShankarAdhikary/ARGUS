"""Voice biometrics endpoints. NOT registered unless VOICEPRINT_ENABLED=true (see main.py): until the evaluation in
docs/voice-biometrics-design.md has been run they answer 404, not 403 or 501, because they must not be discoverable as a
capability. Every request must carry `lawful_interception_ref`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

import platform_api
import voice
import voiceprint
from auth_security import get_current_user, require_role
from config import VOICE_MAX_BYTES
from evidence_api import _authorize_case
from jurisdictions import UNASSIGNED

logger = logging.getLogger("argus.voiceprint")
router = APIRouter(prefix="/api/v1/biometric/voice", tags=["voice-biometrics"])
_index: Optional[voiceprint.VoiceprintIndex] = None


def configure(index: voiceprint.VoiceprintIndex) -> None:
    global _index
    _index = index


def assert_ready_for_production(index: voiceprint.VoiceprintIndex, dev_mode: bool) -> None:
    """Production refuses to start with the endpoints enabled but uncalibrated: that would put uninterpretable scores in front of officers."""
    if not dev_mode and index.calibration is None:
        raise RuntimeError(
            "[ARGUS] VOICEPRINT_ENABLED=true in production but no valid calibration file: run scripts/evaluate_voiceprint.py on real "
            "recordings first (docs/voice-biometrics-design.md, section 7)."
        )


def _idx() -> voiceprint.VoiceprintIndex:
    if _index is None:  # pragma: no cover - configure() is called before the router is included
        raise HTTPException(status_code=503, detail="Voice biometrics is not configured.")
    return _index


def _fail(exc: Exception) -> HTTPException:
    logger.exception("voiceprint failure: %s", exc)
    return HTTPException(status_code=503, detail="An internal error occurred. Contact your system administrator.")


async def _read_audio(file: UploadFile) -> tuple[bytes, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in voice.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Upload WAV, MP3 or M4A audio.")
    audio = await file.read(VOICE_MAX_BYTES + 1)
    if len(audio) > VOICE_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Audio is larger than {VOICE_MAX_BYTES // (1024 * 1024)} MB.")
    if not audio or voice.sniff_audio(audio) is None:
        raise HTTPException(status_code=415, detail="The file does not look like WAV, MP3 or M4A audio.")
    return audio, suffix


def _ref(value: Optional[str], current_user: dict, action: str) -> str:
    """Validate the authorization reference. A missing or placeholder one is refused AND recorded: who tried to use voice
    biometrics without an authorization is exactly what an accountability review looks for."""
    try:
        return voiceprint.validate_lawful_interception_ref(value)
    except ValueError as exc:
        platform_api.log_action(current_user, action="voiceprint_denied", resource=f"{action}:no-valid-authorization",
                                extra={"attempted": action, "reason": "missing or invalid lawful_interception_ref", "supplied": (value or "")[:40]})
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _quality_error(exc: voiceprint.LowQualityAudio) -> HTTPException:
    return HTTPException(status_code=422, detail={"quality_too_low": True, "message": str(exc), "quality_check": exc.quality})


def _engine_error(exc: NotImplementedError) -> HTTPException:
    return HTTPException(status_code=501, detail=str(exc) or voiceprint.UNAVAILABLE_MESSAGE)


@router.get("/status")
async def status(current_user: dict = Depends(get_current_user)):
    idx = _idx()
    scope = platform_api.search_scope(current_user)
    engine = idx.engine_status()
    return {
        "engine": idx.engine.name, "model": idx.engine.model_id, "available": engine["available"], "message": engine["message"],
        "calibrated": idx.calibration is not None, "enrolled": await asyncio.to_thread(idx.count, scope),
        "mode": "calibrated" if idx.calibration else "ranking-only (no threshold: scores cannot be read as a match)",
        "label": voiceprint.LEAD_LABEL,
    }


@router.post("/enroll")
async def enroll(
    name: str = Form(..., min_length=1, max_length=200),
    fir_id: str = Form(..., min_length=1, max_length=100),
    lawful_interception_ref: str = Form(default="", max_length=100),
    case_id: str | None = Form(default=None),
    justification: str | None = Form(default=None),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("supervisor", "admin")),
):
    """Enrol a voice sample recorded under lawful authorization. Supervisor and admin only."""
    ref = _ref(lawful_interception_ref, current_user, "enroll")
    audio, suffix = await _read_audio(file)
    if case_id:
        await asyncio.to_thread(_authorize_case, case_id, justification, current_user)
    idx = _idx()
    try:
        with platform_api._neo4j.session() as session:
            row = session.run("MATCH (f:FIR {id: $id}) RETURN f.jurisdiction AS j", id=fir_id).single()
        jurisdiction = row["j"] if row and row["j"] else UNASSIGNED
        result = await asyncio.to_thread(idx.enroll, name, fir_id, audio, ref, suffix, jurisdiction)
    except voiceprint.LowQualityAudio as exc:
        raise _quality_error(exc) from exc
    except voiceprint.UnreadableAudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise _engine_error(exc) from exc
    except voiceprint.IndexIntegrityError as exc:
        logger.error("voiceprint index integrity failure: %s", exc)
        raise HTTPException(status_code=503, detail="The voiceprint index failed its integrity check. Contact your system administrator.") from exc
    platform_api.log_action(current_user, action="voiceprint_enroll", resource=f"fir:{fir_id[:100]} lir:{ref}", justification=justification,
                            extra={"suspect_id": result["suspect_id"], "lawful_interception_ref": ref, "sample_sha256": result["sample_sha256"],
                                   "duration_seconds": result["duration_seconds"], "net_speech_seconds": result["net_speech_seconds"],
                                   "channel_quality_score": result["channel_quality_score"], "jurisdiction": jurisdiction, "case_id": case_id})
    return {**result, "label": voiceprint.LEAD_LABEL}


@router.post("/match")
async def match(
    lawful_interception_ref: str = Form(default="", max_length=100),
    case_id: str | None = Form(default=None),
    justification: str | None = Form(default=None),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Rank enrolled voices against a clip, within the caller's jurisdiction. A lead only: see `label`."""
    ref = _ref(lawful_interception_ref, current_user, "match")
    audio, suffix = await _read_audio(file)
    if case_id:
        await asyncio.to_thread(_authorize_case, case_id, justification, current_user)
    idx = _idx()
    scope = platform_api.search_scope(current_user)
    sample_sha256 = hashlib.sha256(audio).hexdigest()
    try:
        outcome = await asyncio.to_thread(idx.match, audio, ref, suffix, 5, scope)
    except voiceprint.UnreadableAudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise _engine_error(exc) from exc
    except voiceprint.IndexIntegrityError as exc:
        logger.error("voiceprint index integrity failure: %s", exc)
        raise HTTPException(status_code=503, detail="The voiceprint index failed its integrity check. Contact your system administrator.") from exc
    top = outcome["candidates"][0] if outcome["candidates"] else None
    platform_api.log_action(current_user, action="voiceprint_match", resource=f"case:{case_id} lir:{ref}" if case_id else f"voice:probe lir:{ref}", justification=justification,
                            extra={"lawful_interception_ref": ref, "sample_sha256": sample_sha256, "jurisdiction_filter": scope,
                                   "calibrated": outcome["calibrated"], "quality_passed": not outcome["quality_too_low"],
                                   "duration_seconds": outcome["quality"].get("duration_seconds"), "channel_quality_score": outcome["quality"].get("quality_score"),
                                   "match_found": outcome["match_found"], "top_candidate": top["name"] if top else None, "top_score": top["score"] if top else None})
    if outcome["quality_too_low"]:
        raise HTTPException(status_code=422, detail={"quality_too_low": True, "message": outcome["message"], "quality_check": outcome["quality"]})
    return {
        "match_found": outcome["match_found"], "calibrated": outcome["calibrated"], "quality_check": outcome["quality"],
        "candidates": outcome["candidates"], "lawful_interception_ref": ref,
        "note": None if outcome["calibrated"] else "No decision threshold has been measured yet: candidates are ranked by raw score only and a score must not be read as a match.",
        "label": voiceprint.LEAD_LABEL,
    }
