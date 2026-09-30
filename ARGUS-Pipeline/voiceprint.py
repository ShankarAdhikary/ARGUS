"""Voice biometrics: speaker identification on lawfully authorized recorded or intercepted audio.

Design and rationale: docs/voice-biometrics-design.md. The points that shape this module:

* Every enrolment and match carries `lawful_interception_ref` (validated here, stored on the entry, written to the audit log).
* Raw audio is never stored: only a 192-d ECAPA-TDNN speaker embedding and the sample's SHA-256.
* NO threshold is hard-coded. Without a valid calibration file (written only by scripts/evaluate_voiceprint.py, which refuses
  to run on too little data) the index works in ranking-only mode: raw cosine scores, `match_found: null`, label
  "Uncalibrated: ranking only". Cosine scores have no universal meaning; the value that gives a given false-accept rate
  depends on the channel, language and duration mix.
* The index document lives in the MinIO bucket `voiceprint-index`, HMAC-sealed (own key), shared across workers by a Redis
  lock and ETag, exactly like the fingerprint index.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import importlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, ContextManager, Optional, Protocol

LEAD_LABEL = "Investigative lead — requires forensic voice expert confirmation before use in proceedings."
UNCALIBRATED_LABEL = "Uncalibrated: ranking only"
UNAVAILABLE_MESSAGE = (
    "No speaker-embedding engine is installed. Build the image with INSTALL_VOICEPRINT=true (SpeechBrain ECAPA-TDNN) and set "
    "VOICEPRINT_ENGINE=voiceprint:SpeechBrainEngine, or plug in another engine as package.module:factory."
)
BUCKET = "voiceprint-index"
OBJECT_KEY = "index.json"
KEY_PURPOSE = b"argus-voiceprint-index-v1"
SAMPLE_RATE = 16_000
EMBEDDING_DIM = 192                                                      # ECAPA-TDNN
MIN_SPEECH_SECONDS = float(os.getenv("VOICEPRINT_MIN_SPEECH_SECONDS", "3"))
MAX_AUDIO_SECONDS = int(os.getenv("VOICEPRINT_MAX_SECONDS", "120"))
MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"

# What an evaluation must contain before it is allowed to become a calibration. Shared by scripts/evaluate_voiceprint.py.
REQUIRED_CHANNELS = ("phone", "direct")
CALIBRATION_MIN = {
    "speakers": 50,             # overall
    "speakers_per_cell": 10,    # in every language x channel cell
    "languages": 3,             # Hindi, English and at least one regional language
    "impostor_trials": 10_000,  # ~10 expected errors at a 0.1% false-accept rate
    "impostor_trials_for_far_0_01pct": 100_000,
}


class LowQualityAudio(Exception):
    def __init__(self, message: str, quality: dict):
        super().__init__(message)
        self.quality = quality


class UnreadableAudioError(ValueError):
    """ffmpeg could not decode the upload."""


class IndexIntegrityError(RuntimeError):
    """The persisted index failed its HMAC check and was not loaded."""


# ---------------------------------------------------------------------------
# Lawful-interception reference
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /\\\-_.:()]{2,99}$")
_PLACEHOLDERS = {"na", "n/a", "none", "null", "nil", "test", "testing", "tbd", "pending", "xxx", "xxxx", "unknown", "nothing",
                 "dummy", "sample", "abc", "abcd", "123", "1234", "notapplicable", "notavailable"}


def validate_lawful_interception_ref(ref: Optional[str]) -> str:
    """The authorization order reference, normalised. Raises ValueError for anything that is obviously not one.

    This is a syntactic check only: the system cannot confirm the order exists (that would need an integration with the issuing
    authority's register). It stops blanks and placeholders so the audit trail cannot be filled with junk.
    """
    cleaned = " ".join((ref or "").split())
    compact = re.sub(r"[\s.\-_:/\\()]", "", cleaned).lower()
    if not _REF_RE.match(cleaned) or compact in _PLACEHOLDERS or cleaned.lower() in _PLACEHOLDERS or len(set(compact)) < 3:
        raise ValueError("A valid lawful_interception_ref (the authorization order number) is required.")
    return cleaned


# ---------------------------------------------------------------------------
# Audio: decode and channel quality
# ---------------------------------------------------------------------------

def decode_audio(data: bytes, suffix: str = ".wav", max_seconds: int = MAX_AUDIO_SECONDS):
    """Any audio ffmpeg can read -> mono float32 at 16 kHz (first `max_seconds` only)."""
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=suffix or ".wav") as fh:
        fh.write(data)
        fh.flush()
        try:
            proc = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-t", str(max_seconds), "-i", fh.name, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
                capture_output=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise UnreadableAudioError(f"Audio could not be decoded: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise UnreadableAudioError("Audio could not be decoded (unsupported or corrupt file).")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def channel_quality(wave, sr: int = SAMPLE_RATE) -> dict:
    """Heuristic usability of a voice sample. NOT a standard measure and uncalibrated.

    net_speech_seconds  speech left after an energy-based silence removal (frames 12 dB above the quietest 10%)
    snr_db              speech energy over noise-floor energy
    clipping_ratio      share of samples at full scale
    bandwidth_hz        95% spectral roll-off of the speech; below ~4.2 kHz is flagged narrowband (telephone)
    quality_score       0-100 blend of the four; only `passed` (enough net speech) gates anything
    """
    import numpy as np

    w = np.asarray(wave, dtype=np.float64)
    duration = len(w) / sr
    empty = {"passed": False, "duration_seconds": round(duration, 2), "net_speech_seconds": 0.0, "snr_db": 0.0, "clipping_ratio": 0.0,
             "bandwidth_hz": 0.0, "narrowband": False, "quality_score": 0.0, "method": "energy-vad+snr+bandwidth (heuristic)"}
    frame, hop = int(0.025 * sr), int(0.010 * sr)
    if len(w) < frame * 4:
        return {**empty, "reason": "Audio is too short."}
    frames = np.lib.stride_tricks.sliding_window_view(w, frame)[::hop]
    energy = (frames ** 2).mean(axis=1) + 1e-12
    db = 10 * np.log10(energy)
    floor = float(np.percentile(db, 10))
    speech = (db > floor + 12) & (db > -60)
    net = float(speech.sum() * hop / sr)
    if not speech.any():
        return {**empty, "reason": f"No speech found ({duration:.1f} s of audio, none above the noise floor)."}
    noise_e = float(energy[db <= floor + 3].mean())
    snr = float(10 * np.log10(energy[speech].mean() / max(noise_e, 1e-12)))
    clipping = float((np.abs(w) >= 0.99).mean())
    spectrum = (np.abs(np.fft.rfft(frames[speech] * np.hanning(frame), axis=1)) ** 2).mean(axis=0)
    cumulative = np.cumsum(spectrum) / spectrum.sum()
    rolloff = float(np.fft.rfftfreq(frame, 1 / sr)[min(int(np.searchsorted(cumulative, 0.95)), len(cumulative) - 1)])
    score = 100.0 * (0.40 * min(1.0, net / 20.0) + 0.35 * min(1.0, max(0.0, (snr - 5.0) / 25.0))
                     + 0.15 * (1.0 - min(1.0, clipping * 20.0)) + 0.10 * min(1.0, rolloff / 6000.0))
    passed = net >= MIN_SPEECH_SECONDS
    return {"passed": passed, "duration_seconds": round(duration, 2), "net_speech_seconds": round(net, 2), "snr_db": round(snr, 1),
            "clipping_ratio": round(clipping, 4), "bandwidth_hz": round(rolloff), "narrowband": rolloff < 4200.0,
            "quality_score": round(score, 1), "method": "energy-vad+snr+bandwidth (heuristic)",
            "reason": None if passed else f"Not enough speech: {net:.1f} s of speech found, at least {MIN_SPEECH_SECONDS:g} s needed."}


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class VoiceEngine(Protocol):
    name: str
    model_id: str

    def embed(self, wave) -> "np.ndarray": ...      # noqa: F821  unit-length float32 vector


class UnavailableEngine:
    name = "unavailable"
    model_id = "none"

    def embed(self, wave):
        raise NotImplementedError(UNAVAILABLE_MESSAGE)

    def not_ready_reason(self) -> Optional[str]:
        return UNAVAILABLE_MESSAGE


class SpeechBrainEngine:
    """SpeechBrain ECAPA-TDNN (spkrec-ecapa-voxceleb) on CPU. The weights are baked into the image (VOICEPRINT_MODEL_DIR)."""

    name = "speechbrain-ecapa-tdnn"
    model_id = MODEL_ID

    def __init__(self, savedir: Optional[str] = None):
        self.savedir = savedir or os.getenv("VOICEPRINT_MODEL_DIR", "/opt/speechbrain-cache/ecapa")
        self._lock = threading.Lock()
        self._clf = None

    def not_ready_reason(self) -> Optional[str]:
        for module in ("torch", "speechbrain"):
            if importlib.util.find_spec(module) is None:
                return f"{module} is not installed: build the image with INSTALL_VOICEPRINT=true."
        return None

    def _ensure(self):
        with self._lock:
            if self._clf is None:
                reason = self.not_ready_reason()
                if reason:
                    raise NotImplementedError(f"Speaker engine unavailable: {reason}")
                try:
                    from speechbrain.inference.speaker import EncoderClassifier

                    self._clf = EncoderClassifier.from_hparams(source=self.model_id, savedir=self.savedir, run_opts={"device": "cpu"})
                except Exception as exc:  # missing weights and no network, or an incompatible library
                    raise NotImplementedError(f"Speaker engine unavailable: could not load {self.model_id}: {exc}") from exc
            return self._clf

    def embed(self, wave):
        import numpy as np
        import torch

        clf = self._ensure()
        with torch.no_grad():
            emb = clf.encode_batch(torch.from_numpy(np.ascontiguousarray(wave, dtype=np.float32)).unsqueeze(0))
        vec = emb.squeeze().cpu().numpy().astype(np.float32)
        return vec / max(float(np.linalg.norm(vec)), 1e-12)


def load_engine(spec: Optional[str] = None) -> VoiceEngine:
    spec = spec if spec is not None else os.getenv("VOICEPRINT_ENGINE", "")
    if not spec:
        return UnavailableEngine()
    module, _, factory = spec.partition(":")
    try:
        return getattr(importlib.import_module(module), factory or "create_engine")()
    except Exception as exc:
        print(f"[!] Could not load voiceprint engine {spec!r}: {exc}")
        return UnavailableEngine()


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

@dataclass
class Calibration:
    model_id: str
    created_at: str
    thresholds: dict            # eer, far_1pct, far_0_1pct, far_0_01pct (None when there were too few impostor trials)
    counts: dict = field(default_factory=dict)


def calibration_shortfalls(counts: dict) -> list[str]:
    """Why an evaluation is not enough to become a calibration (empty list = enough). Used by the service and the script."""
    problems = []
    if counts.get("speakers", 0) < CALIBRATION_MIN["speakers"]:
        problems.append(f"{counts.get('speakers', 0)} speakers, need at least {CALIBRATION_MIN['speakers']}")
    if len(counts.get("languages", [])) < CALIBRATION_MIN["languages"]:
        problems.append(f"{len(counts.get('languages', []))} languages, need at least {CALIBRATION_MIN['languages']} (Hindi, English and a regional language)")
    missing = [c for c in REQUIRED_CHANNELS if c not in counts.get("channels", [])]
    if missing:
        problems.append("missing channel(s): " + ", ".join(missing))
    thin = {k: v for k, v in (counts.get("speakers_per_cell") or {}).items() if v < CALIBRATION_MIN["speakers_per_cell"]}
    if thin or not counts.get("speakers_per_cell"):
        problems.append(f"every language x channel cell needs at least {CALIBRATION_MIN['speakers_per_cell']} speakers; short: {thin or 'no cell data'}")
    if counts.get("impostor_trials", 0) < CALIBRATION_MIN["impostor_trials"]:
        problems.append(f"{counts.get('impostor_trials', 0)} impostor trials, need at least {CALIBRATION_MIN['impostor_trials']}")
    return problems


def load_calibration(path: Optional[str], model_id: str) -> tuple[Optional[Calibration], Optional[str]]:
    """(calibration, None) or (None, why it was not used). Never guesses: any doubt means ranking-only mode."""
    if not path or not Path(path).is_file():
        return None, "no calibration file (the evaluation has not been run)"
    try:
        doc = json.loads(Path(path).read_text())
        thresholds = doc["thresholds"]
        counts = doc["counts"]
        if doc.get("model_id") != model_id:
            return None, f"calibration was made for model {doc.get('model_id')!r}, not {model_id!r}: re-run the evaluation"
        short = calibration_shortfalls(counts)
        if short:
            return None, "calibration does not meet the minimum data requirements: " + "; ".join(short)
        if not isinstance(thresholds.get("far_0_1pct"), (int, float)):
            return None, "calibration has no usable 0.1% false-accept threshold"
        return Calibration(doc["model_id"], doc.get("created_at", ""), thresholds, counts), None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"calibration file unreadable: {exc}"


def confidence_label(score: float, calibration: Optional[Calibration]) -> str:
    if calibration is None:
        return UNCALIBRATED_LABEL
    high = calibration.thresholds.get("far_0_01pct")
    if isinstance(high, (int, float)) and score >= high:
        return "High"
    return "Medium" if score >= calibration.thresholds["far_0_1pct"] else "Below threshold"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VoiceprintIndex:
    """Sealed voiceprint store with exact cosine search. `store` and `lock` are the fingerprint module's MinioStore / redis_lock."""

    def __init__(self, store, engine: Optional[VoiceEngine] = None, *, key: bytes, lock: Optional[Callable[[], ContextManager]] = None,
                 calibration: Optional[Calibration] = None, decoder: Callable = decode_audio):
        self.store, self.engine, self.key, self.calibration, self._decode = store, engine or load_engine(), key, calibration, decoder
        self._lock = lock or (lambda: contextlib.nullcontext())
        self._mutex = threading.RLock()
        self._entries: list[dict] = []
        self._etag: Optional[str] = None
        self._loaded = False
        self._matrix = None            # (n, 192) float32 of ALL entries (tombstones are zero rows), rebuilt when the ETag changes
        self._matrix_etag: Optional[str] = None

    # -- persistence (same envelope as the fingerprint index) --------------------
    def _seal(self, entries: list[dict]) -> bytes:
        payload = json.dumps({"entries": entries}, separators=(",", ":"), sort_keys=True)
        return json.dumps({"v": 1, "hmac": hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest(), "payload": payload}).encode()

    def _open(self, blob: bytes) -> list[dict]:
        try:
            envelope = json.loads(blob)
            payload = envelope["payload"]
            if not hmac.compare_digest(hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest(), str(envelope["hmac"])):
                raise IndexIntegrityError("Voiceprint index failed its integrity check and was not loaded.")
            return json.loads(payload)["entries"]
        except IndexIntegrityError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise IndexIntegrityError("Voiceprint index is malformed and was not loaded.") from exc

    def _sync(self) -> None:
        etag = self.store.etag()
        if self._loaded and etag == self._etag:
            return
        blob = self.store.get() if etag is not None else None
        self._entries = self._open(blob) if blob else []
        self._etag, self._loaded = etag, True

    def _save(self, entries: list[dict]) -> None:
        self.store.put(self._seal(entries))
        self._entries, self._etag = entries, self.store.etag()

    def _vectors(self):
        import numpy as np

        if self._matrix is None or self._matrix_etag != self._etag:
            rows = [np.frombuffer(base64.b64decode(e["embedding"]), dtype=np.float32) if e.get("embedding") and not e.get("deleted")
                    else np.zeros(EMBEDDING_DIM, dtype=np.float32) for e in self._entries]
            self._matrix = np.vstack(rows) if rows else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            self._matrix_etag = self._etag
        return self._matrix

    def engine_status(self) -> dict:
        reason = getattr(self.engine, "not_ready_reason", lambda: None)()
        return {"available": reason is None and not isinstance(self.engine, UnavailableEngine), "message": reason}

    def count(self, scope: Optional[str] = None) -> int:
        with self._mutex:
            self._sync()
            return sum(1 for e in self._entries if not e.get("deleted") and (scope is None or e.get("jurisdiction") == scope))

    # -- API ----------------------------------------------------------------------
    def _prepare(self, audio: bytes, suffix: str) -> tuple:
        """Decode, check quality (raises LowQualityAudio), embed. Returns (embedding, quality)."""
        wave = self._decode(audio, suffix)
        quality = channel_quality(wave)
        if not quality["passed"]:
            raise LowQualityAudio(quality["reason"], {k: v for k, v in quality.items() if k != "reason"})
        return self.engine.embed(wave), quality

    def enroll(self, name: str, fir_id: str, audio: bytes, lawful_interception_ref: str, suffix: str = ".wav", jurisdiction: Optional[str] = None) -> dict:
        """Enrol one voice sample. Raises ValueError (bad reference), LowQualityAudio, UnreadableAudioError, NotImplementedError."""
        ref = validate_lawful_interception_ref(lawful_interception_ref)
        embedding, quality = self._prepare(audio, suffix)
        entry = {
            "suspect_id": str(uuid.uuid4()), "name": name, "fir_id": fir_id, "jurisdiction": jurisdiction or "Unassigned",
            "lawful_interception_ref": ref, "enrolled_at": _now(), "duration_seconds": quality["duration_seconds"],
            "net_speech_seconds": quality["net_speech_seconds"], "channel_quality_score": quality["quality_score"],
            "sample_sha256": hashlib.sha256(audio).hexdigest(), "model_id": self.engine.model_id,
            "embedding": base64.b64encode(embedding.astype("float32").tobytes()).decode(), "deleted": False,
        }
        with self._mutex, self._lock():
            self._sync()
            self._save([*self._entries, entry])
        public = {k: entry[k] for k in ("suspect_id", "name", "fir_id", "jurisdiction", "lawful_interception_ref", "enrolled_at",
                                        "duration_seconds", "net_speech_seconds", "channel_quality_score", "sample_sha256")}
        return {**public, "quality": {k: v for k, v in quality.items() if k != "reason"}}

    def match(self, audio: bytes, lawful_interception_ref: str, suffix: str = ".wav", top_k: int = 5, scope: Optional[str] = None) -> dict:
        """Rank enrolled voices against `audio`.

        Uncalibrated: the top_k by raw cosine score, `match_found: None`, label "Uncalibrated: ranking only".
        Calibrated:   only candidates at or above the measured 0.1% false-accept threshold, `match_found: bool`.
        Poor audio never raises: {"quality_too_low": True, ...}.
        """
        import numpy as np

        ref = validate_lawful_interception_ref(lawful_interception_ref)
        try:
            probe, quality = self._prepare(audio, suffix)
        except LowQualityAudio as exc:
            return {"quality_too_low": True, "message": str(exc), "quality": exc.quality, "calibrated": self.calibration is not None,
                    "match_found": None if self.calibration is None else False, "candidates": [], "lawful_interception_ref": ref}
        with self._mutex:
            self._sync()
            matrix = self._vectors()
            entries = list(self._entries)
        quality = {k: v for k, v in quality.items() if k != "reason"}
        scores = matrix @ probe.astype(np.float32) if len(entries) else np.zeros(0, dtype=np.float32)
        eligible = [i for i, e in enumerate(entries) if not e.get("deleted") and (scope is None or e.get("jurisdiction") == scope)]
        ranked = sorted(eligible, key=lambda i: -float(scores[i]))
        floor = None if self.calibration is None else float(self.calibration.thresholds["far_0_1pct"])
        candidates = []
        for i in ranked:
            score = float(scores[i])
            if floor is not None and score < floor:
                break
            e = entries[i]
            candidates.append({"rank": len(candidates) + 1, "name": e["name"], "fir_id": e["fir_id"], "jurisdiction": e.get("jurisdiction"),
                               "score": round(score, 4), "confidence_label": confidence_label(score, self.calibration),
                               "enrolled_quality_score": e.get("channel_quality_score")})
            if len(candidates) >= top_k:
                break
        return {"quality_too_low": False, "quality": quality, "calibrated": self.calibration is not None,
                "match_found": None if self.calibration is None else bool(candidates), "candidates": candidates, "lawful_interception_ref": ref}

    def delete_by_fir(self, fir_id: str) -> int:
        """Tombstone: the embedding is removed, the record (and its authorization reference) stays for audit."""
        with self._mutex, self._lock():
            self._sync()
            removed, entries = 0, []
            for entry in self._entries:
                if entry.get("fir_id") == fir_id and not entry.get("deleted"):
                    entry = {**entry, "deleted": True, "embedding": None, "deleted_at": _now()}
                    removed += 1
                entries.append(entry)
            if removed:
                self._save(entries)
            return removed
