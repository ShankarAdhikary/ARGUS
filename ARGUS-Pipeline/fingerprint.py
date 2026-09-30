"""Fingerprint identification index (latent and rolled prints).

Why this does not use FAISS: a fingerprint template is a variable-size set of minutiae, and matchers such as SourceAFIS
score a *pair* of templates by aligning their minutiae graphs. There is no fixed-length vector for an L2 index to
compare, so a FAISS search over templates would rank by a meaningless distance. Matching is therefore a 1:N scan that
asks the engine to score the probe against each enrolled template (SourceAFIS builds its probe index once, so each
comparison is cheap).

The comparison engine is pluggable via `FINGERPRINT_ENGINE=package.module:factory`. SourceAFIS has no PyPI package (it is a
Java library with a .NET port), so the bundled real engine, `JvmSourceAFISEngine`, drives the official Java library through
a JPype bridge (JRE and pinned jars come from the Dockerfile). With no engine configured the default is an
`UnavailableEngine` that raises NotImplementedError with an explanation, and the API answers 501. Any object with
`extract(image_bytes) -> Template` and `score(probe, candidate) -> float` can be plugged in.

Scores are specific to the engine that produced them. SourceAFIS scores are NOT comparable with scores from other AFIS
products, and SourceAFIS targets rolled and plain prints: it has not been validated for latent (crime-scene) prints.

Persistence: one JSON document in the MinIO bucket `fingerprint-index`, wrapped in an HMAC envelope. The key is derived
from the JWT secret, so anyone able to write to the bucket cannot inject a template that would later "match"; a snapshot
whose HMAC does not verify is refused. Templates are stored base64 inside the JSON (never pickled, so a tampered
bucket cannot execute code on load). Workers and processes stay in step through a shared lock and the object's ETag.

Deleting by FIR leaves a tombstone (metadata kept for audit, template bytes removed).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import importlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, ContextManager, Optional, Protocol

MATCH_THRESHOLD = float(os.getenv("FINGERPRINT_MATCH_THRESHOLD", "40"))   # SourceAFIS: ~40 is its documented FMR 0.01% operating point
HIGH_CONFIDENCE = 70.0
MIN_MINUTIAE = int(os.getenv("FINGERPRINT_MIN_MINUTIAE", "12"))          # below this a print is too sparse to search
FULL_QUALITY_MINUTIAE = 40                                              # minutiae count that maps to a quality score of 100
BUCKET = "fingerprint-index"
OBJECT_KEY = "index.json"
LEAD_LABEL = "Investigative lead — verify before use"
DISCLAIMER = (
    "Investigative lead — verify before use. Fingerprint evidence requires forensic lab confirmation. "
    "Scores come from one algorithm (SourceAFIS) and are not comparable with scores from other AFIS products; "
    "SourceAFIS is built for rolled and plain prints and has not been validated for latent prints."
)
PRINT_TYPES = ("rolled", "latent")
UNAVAILABLE_MESSAGE = (
    "No fingerprint engine is installed. SourceAFIS has no PyPI package (it is a Java library with a .NET port) and no "
    "maintained pure-Python port exists. Install a matcher and set FINGERPRINT_ENGINE=package.module:factory, or bridge "
    "the Java SourceAFIS library (requires a JVM)."
)


@dataclass
class Template:
    data: bytes          # engine-specific serialised template
    minutiae: int        # minutiae found; drives the quality check
    native: object = None   # engine-side object cached for the duration of one request (never persisted)


class LowQualityError(Exception):
    def __init__(self, message: str, quality_score: float = 0.0, quality: Optional[dict] = None):
        super().__init__(message)
        self.quality_score = quality_score
        self.quality = quality      # the full quality_check dict when the gate produced one


class IndexIntegrityError(RuntimeError):
    """The persisted index failed its HMAC check and was not loaded."""


class FingerprintEngine(Protocol):
    name: str

    def extract(self, image_bytes: bytes) -> Template: ...
    def score(self, probe: Template, candidate: Template) -> float: ...


class UnavailableEngine:
    name = "unavailable"

    def extract(self, image_bytes: bytes) -> Template:
        raise NotImplementedError(UNAVAILABLE_MESSAGE)

    def score(self, probe: Template, candidate: Template) -> float:
        raise NotImplementedError(UNAVAILABLE_MESSAGE)


def load_engine(spec: Optional[str] = None) -> FingerprintEngine:
    """Engine named by FINGERPRINT_ENGINE ('module:factory'), else the explanatory stub."""
    spec = spec if spec is not None else os.getenv("FINGERPRINT_ENGINE", "")
    if not spec:
        return UnavailableEngine()
    module, _, factory = spec.partition(":")
    try:
        return getattr(importlib.import_module(module), factory or "create_engine")()
    except Exception as exc:  # a misconfigured engine must not take the API down
        print(f"[!] Could not load fingerprint engine {spec!r}: {exc}")
        return UnavailableEngine()


def quality_of(template: Template) -> dict:
    """Quality from the minutiae count. This is NOT an NFIQ score (SourceAFIS has none); `nfiq_score` stays null."""
    score = min(100.0, round(100.0 * template.minutiae / FULL_QUALITY_MINUTIAE, 1))
    return {"passed": template.minutiae >= MIN_MINUTIAE, "quality_score": score, "nfiq_score": None,
            "method": "minutiae-count", "minutiae": template.minutiae, "minimum_minutiae": MIN_MINUTIAE}


def confidence_label(score: float, print_type: str = "rolled") -> str:
    """High >= 70, Medium 40-69, else Low / insufficient. For a latent probe the label says so, because a field officer
    reading "Medium" should know it came from a crime-scene fragment and not from a rolled print."""
    band = "High" if score >= HIGH_CONFIDENCE else "Medium" if score >= MATCH_THRESHOLD else None
    if band is None:
        return "Low / insufficient"
    return f"{band} / latent match" if print_type == "latent" else band


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class Store(Protocol):
    def etag(self) -> Optional[str]: ...
    def get(self) -> Optional[bytes]: ...
    def put(self, data: bytes) -> None: ...


class MinioStore:
    """The index document in a MinIO bucket (created on first use)."""

    def __init__(self, client, bucket: str = BUCKET, key: str = OBJECT_KEY):
        self.client, self.bucket, self.key = client, bucket, key

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def etag(self) -> Optional[str]:
        try:
            return self.client.stat_object(self.bucket, self.key).etag
        except Exception as exc:
            if getattr(exc, "code", "") in {"NoSuchKey", "NoSuchBucket", "NotFound"}:
                return None
            raise

    def get(self) -> Optional[bytes]:
        try:
            response = self.client.get_object(self.bucket, self.key)
        except Exception as exc:
            if getattr(exc, "code", "") in {"NoSuchKey", "NoSuchBucket", "NotFound"}:
                return None
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def put(self, data: bytes) -> None:
        import io

        self._ensure_bucket()
        self.client.put_object(self.bucket, self.key, io.BytesIO(data), len(data), content_type="application/json")


def redis_lock(client, name: str = "argus:fingerprint-index") -> Callable[[], ContextManager]:
    """Lock shared by every worker and container (enrolment rewrites the whole document)."""
    return lambda: client.lock(name, timeout=120, blocking_timeout=60)


def derive_key(secret: str) -> bytes:
    """Purpose-specific HMAC key derived from an existing Vault-managed secret (no new secret to distribute)."""
    return hmac.new(secret.encode(), b"argus-fingerprint-index-v1", hashlib.sha256).digest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class FingerprintIndex:
    def __init__(self, store: Store, engine: Optional[FingerprintEngine] = None, *, key: bytes,
                 lock: Optional[Callable[[], ContextManager]] = None, threshold: float = MATCH_THRESHOLD):
        self.store, self.engine, self.key, self.threshold = store, engine or load_engine(), key, threshold
        self._lock = lock or (lambda: contextlib.nullcontext())
        self._mutex = threading.RLock()
        self._entries: list[dict] = []
        self._etag: Optional[str] = None
        self._loaded = False

    # -- persistence -------------------------------------------------------
    def _seal(self, entries: list[dict]) -> bytes:
        payload = json.dumps({"entries": entries}, separators=(",", ":"), sort_keys=True)
        mac = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return json.dumps({"v": 1, "hmac": mac, "payload": payload}).encode()

    def _open(self, blob: bytes) -> list[dict]:
        try:
            envelope = json.loads(blob)
            payload = envelope["payload"]
            expected = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, str(envelope["hmac"])):
                raise IndexIntegrityError("Fingerprint index failed its integrity check and was not loaded.")
            return json.loads(payload)["entries"]
        except IndexIntegrityError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise IndexIntegrityError("Fingerprint index is malformed and was not loaded.") from exc

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

    def _precheck(self, image_bytes: bytes) -> Optional[dict]:
        """Cheap image-quality gate run BEFORE minutiae extraction and matching, if the engine provides one."""
        gate = getattr(self.engine, "precheck", None)
        if gate is None:
            return None
        quality = gate(image_bytes)
        if not quality["passed"]:
            raise LowQualityError(quality["reason"], quality["quality_score"], {k: v for k, v in quality.items() if k != "reason"})
        return quality

    def engine_status(self) -> dict:
        """{available, message}: available only if an engine is configured AND actually able to run."""
        if isinstance(self.engine, UnavailableEngine):
            return {"available": False, "message": UNAVAILABLE_MESSAGE}
        ready = getattr(self.engine, "not_ready_reason", lambda: None)()
        return {"available": ready is None, "message": ready}

    # -- API ---------------------------------------------------------------
    def _live(self, scope: Optional[str] = None) -> list[dict]:
        return [e for e in self._entries if not e.get("deleted") and (scope is None or e.get("jurisdiction") == scope)]

    def count(self, scope: Optional[str] = None) -> int:
        with self._mutex:
            self._sync()
            return len(self._live(scope))

    def enroll_batch(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """Enrol many prints with a single index rewrite. items: [{name, fir_id, image, jurisdiction?, label?}].

        Returns (enrolled, errors). NotImplementedError (no engine) propagates: nothing can be enrolled without one.
        """
        enrolled, errors, fresh = [], [], []
        for item in items:
            label = item.get("label") or item["fir_id"]
            try:
                self._precheck(item["image"])
                template = self.engine.extract(item["image"])
                quality = quality_of(template)
                if not quality["passed"]:
                    raise LowQualityError(f"Print quality too low ({template.minutiae} minutiae, need {MIN_MINUTIAE}).", quality["quality_score"])
            except LowQualityError as exc:
                errors.append({"file": label, "error": str(exc), "quality_too_low": True, "quality_score": exc.quality_score})
                continue
            except NotImplementedError:
                raise
            except Exception as exc:  # an unreadable image must not abort a whole batch
                errors.append({"file": label, "error": f"Could not read print: {exc}"})
                continue
            entry = {
                "suspect_id": str(uuid.uuid4()), "name": item["name"], "fir_id": item["fir_id"],
                "jurisdiction": item.get("jurisdiction") or "Unassigned", "enrolled_at": _now(),
                "minutiae": template.minutiae, "template": base64.b64encode(template.data).decode(), "deleted": False,
            }
            fresh.append(entry)
            enrolled.append({k: entry[k] for k in ("suspect_id", "name", "fir_id", "jurisdiction", "enrolled_at")})
        if fresh:
            with self._mutex, self._lock():
                self._sync()  # pick up what other workers wrote before appending
                self._save([*self._entries, *fresh])
        return enrolled, errors

    def enroll(self, name: str, fir_id: str, image_bytes: bytes, jurisdiction: Optional[str] = None) -> str:
        """Enrol one print and return its suspect_id. Raises LowQualityError / NotImplementedError."""
        enrolled, errors = self.enroll_batch([{"name": name, "fir_id": fir_id, "image": image_bytes, "jurisdiction": jurisdiction}])
        if errors:
            err = errors[0]
            raise LowQualityError(err["error"], err.get("quality_score", 0.0)) if err.get("quality_too_low") else RuntimeError(err["error"])
        return enrolled[0]["suspect_id"]

    def match(self, image_bytes: bytes, top_k: int = 5, scope: Optional[str] = None) -> dict:
        """Search the index. Never raises for a poor print: returns `quality_too_low` instead.

        {quality_too_low, quality_score, quality, hits: [{rank, suspect_id, name, fir_id, jurisdiction, score}]}
        `hits` are the best-scoring enrolled prints in `scope` (all of them, above or below the threshold); the caller
        applies `threshold`. Raises NotImplementedError when no engine is installed.
        """
        try:
            gate = self._precheck(image_bytes)
            probe = self.engine.extract(image_bytes)
        except LowQualityError as exc:
            quality = exc.quality or {"passed": False, "quality_score": exc.quality_score, "nfiq_score": None, "method": "engine"}
            return {"quality_too_low": True, "quality_score": exc.quality_score, "nfiq_score": None, "quality": quality, "message": str(exc), "hits": []}
        quality = quality_of(probe)
        if gate:   # both measures ran: report the weaker one, and say so
            quality = {**gate, **quality, "quality_score": min(gate["quality_score"], quality["quality_score"]),
                       "method": f"min({gate['method']}, minutiae-count)"}
        if not quality["passed"]:
            return {"quality_too_low": True, "quality_score": quality["quality_score"], "nfiq_score": None, "quality": quality, "hits": []}
        with self._mutex:
            self._sync()
            candidates = list(self._live(scope))
        scored = []
        for entry in candidates:
            candidate = Template(base64.b64decode(entry["template"]), entry.get("minutiae", 0))
            scored.append((float(self.engine.score(probe, candidate)), entry))
        scored.sort(key=lambda pair: -pair[0])
        hits = [
            {"rank": rank, "suspect_id": e["suspect_id"], "name": e["name"], "fir_id": e["fir_id"],
             "jurisdiction": e.get("jurisdiction"), "score": round(s, 1)}
            for rank, (s, e) in enumerate(scored[:top_k], start=1)
        ]
        return {"quality_too_low": False, "quality_score": quality["quality_score"], "nfiq_score": None, "quality": quality, "hits": hits}

    def delete_by_fir(self, fir_id: str) -> int:
        """Tombstone every print enrolled under `fir_id`: metadata stays for audit, the template bytes are removed."""
        with self._mutex, self._lock():
            self._sync()
            removed = 0
            entries = []
            for entry in self._entries:
                if entry.get("fir_id") == fir_id and not entry.get("deleted"):
                    entry = {**entry, "deleted": True, "template": None, "deleted_at": _now()}
                    removed += 1
                entries.append(entry)
            if removed:
                self._save(entries)
            return removed


def parse_zip_member_name(filename: str) -> Optional[tuple[str, str]]:
    """'{name}__{fir_id}.jpg' -> (name, fir_id), or None if the file does not follow the convention.

    Splits on the LAST double underscore so names may contain single underscores; the path is discarded.
    """
    import re

    base = os.path.basename(filename.replace("\\", "/"))
    m = re.match(r"^(?P<stem>.+)\.(?:jpe?g|png|bmp|tiff?)$", base, flags=re.IGNORECASE)
    if not m or "__" not in m.group("stem"):
        return None
    name, _, fir_id = m.group("stem").rpartition("__")
    name, fir_id = " ".join(name.replace("_", " ").split()), fir_id.strip()
    return (name, fir_id) if name and fir_id else None


# ---------------------------------------------------------------------------
# Image-quality gate (numpy only; NOT NFIQ2)
# ---------------------------------------------------------------------------

GATE_BLOCK = 16                                              # px; a ridge period is ~8-10 px at 500 DPI
GATE_MIN_VALID_BLOCKS = int(os.getenv("FINGERPRINT_GATE_MIN_BLOCKS", "16"))     # ~ 8 mm^2 of clear ridges at 500 DPI
GATE_MIN_VALID_RATIO = float(os.getenv("FINGERPRINT_GATE_MIN_RATIO", "0.35"))   # of the ink-bearing blocks
GATE_MIN_CONTRAST = float(os.getenv("FINGERPRINT_GATE_MIN_CONTRAST", "15"))     # grey-level swing of the ridges; catches blur and washed-out prints
GATE_METHOD = "variance+ridge-frequency"


def gray_quality(gray, dpi: float = 500.0) -> dict:
    """Quick usability check of a fingerprint image: how much of it holds clear, evenly spaced ridges.

    Splits the image into 16 px blocks; a block counts as ink-bearing if its variance is a meaningful share of the
    image's strongest blocks, and as *valid* if its dominant spatial frequency is a ridge period (4-16 px, scaled by DPI)
    with a clear peak. `quality_score` mixes the share of valid blocks with how many there are, so a small but clean
    latent fragment is not punished for being small, only a smudged, washed-out or blank image is. Ridge contrast is checked
    in absolute grey levels because blurring keeps a smooth periodic pattern (so the frequency test alone passes it) while
    destroying the minutiae.

    This is a cheap heuristic that saves matcher time on hopeless images. It is NOT NFIQ2 (ISO/IEC 29794-4) and its scores
    are not comparable with NFIQ2's. The default thresholds were tuned on three impressions of one finger (usable prints:
    contrast 24-73; blurred-to-destruction 7.7, washed-out 8.7): a starting point, not a validated operating point, so tune
    them on your own prints (FINGERPRINT_GATE_MIN_*).
    """
    import numpy as np

    g = np.asarray(gray, dtype=np.float64)
    h, w = g.shape[:2]
    B = GATE_BLOCK
    empty = {"passed": False, "quality_score": 0.0, "nfiq_score": None, "method": GATE_METHOD, "valid_blocks": 0, "ink_blocks": 0}
    if h < 4 * B or w < 4 * B:
        return {**empty, "reason": f"Image too small ({w}x{h}px): fingerprint images need at least {4 * B}x{4 * B} px."}
    blocks = g[: h // B * B, : w // B * B].reshape(h // B, B, w // B, B).swapaxes(1, 2)        # (rows, cols, B, B)
    var = blocks.var(axis=(2, 3))
    ink = var >= max(25.0, 0.1 * float(np.percentile(var, 95)))       # std >= 5 grey levels and not far below the best blocks
    n_ink = int(ink.sum())
    if n_ink == 0:
        return {**empty, "reason": "No ridge detail found: the image looks blank or uniform."}
    window = np.outer(np.hanning(B), np.hanning(B))
    freq = np.fft.fftfreq(B)
    fy, fx = np.meshgrid(freq, freq, indexing="ij")
    radius = np.hypot(fy, fx)                                          # cycles per pixel
    scale = dpi / 500.0
    band = (radius >= 1.0 / (16.0 * scale)) & (radius <= 1.0 / (4.0 * scale))
    valid, valid_std = 0, []
    for blk, blk_var in zip(blocks[ink], var[ink]):
        mag = np.abs(np.fft.fft2((blk - blk.mean()) * window))
        mag[radius < 1.0 / B * 1.5] = 0.0                              # drop DC and the slowest gradients
        peak = mag.max()
        if peak > 0 and band.flat[int(mag.argmax())] and peak >= 3.0 * mag[mag > 0].mean():
            valid += 1
            valid_std.append(float(blk_var) ** 0.5)
    ratio = valid / n_ink
    contrast = round(float(np.median(valid_std)), 1) if valid_std else 0.0     # grey-level swing of the clear ridges (0-255 scale)
    score = round(100.0 * (0.5 * ratio + 0.25 * min(1.0, valid / 100.0) + 0.25 * min(1.0, contrast / 40.0)), 1)
    passed = valid >= GATE_MIN_VALID_BLOCKS and ratio >= GATE_MIN_VALID_RATIO and contrast >= GATE_MIN_CONTRAST
    if not passed:
        score = min(score, 39.0)      # a refused print must not display a healthy-looking score
    if passed:
        reason = None
    elif contrast < GATE_MIN_CONTRAST and valid >= GATE_MIN_VALID_BLOCKS:
        reason = f"Print quality too low: the ridges are too faint or blurred (contrast {contrast:.0f}, need at least {GATE_MIN_CONTRAST:.0f})."
    else:
        reason = (f"Print quality too low: only {valid} clear ridge blocks ({ratio:.0%} of the inked area); "
                  f"need at least {GATE_MIN_VALID_BLOCKS} and {GATE_MIN_VALID_RATIO:.0%}.")
    return {"passed": passed, "quality_score": score, "nfiq_score": None, "method": GATE_METHOD,
            "valid_blocks": valid, "ink_blocks": n_ink, "contrast": contrast, "reason": reason}


# ---------------------------------------------------------------------------
# SourceAFIS engine (Java library through JPype)
# ---------------------------------------------------------------------------

class JvmSourceAFISEngine:
    """Real engine: the official SourceAFIS 3.x Java library, driven in-process through a JPype bridge.

    Enable with FINGERPRINT_ENGINE=fingerprint:JvmSourceAFISEngine. The JVM starts lazily on first use (never at import,
    so it is created after uvicorn forks its workers). Jars live in SOURCEAFIS_JAR_DIR (default /opt/sourceafis).
    Thread-safe: JPype attaches Python threads to the JVM automatically.
    """

    name = "sourceafis-jvm"

    def __init__(self, jar_dir: Optional[str] = None, dpi: Optional[float] = None):
        self.jar_dir = jar_dir or os.getenv("SOURCEAFIS_JAR_DIR", "/opt/sourceafis")
        self.dpi = float(dpi if dpi is not None else os.getenv("FINGERPRINT_DPI", "500"))
        self._lock = threading.Lock()
        self._cls: Optional[dict] = None
        self._error: Optional[str] = None

    # -- readiness -----------------------------------------------------------
    def _jars(self) -> list[str]:
        import glob

        return sorted(glob.glob(os.path.join(self.jar_dir, "*.jar")))

    def not_ready_reason(self) -> Optional[str]:
        """None when the engine can run; otherwise why not. Cheap: does not start the JVM."""
        try:
            import jpype  # noqa: F401
        except ImportError:
            return "The JPype bridge (jpype1) is not installed."
        if not any(os.path.basename(j).startswith("sourceafis-") for j in self._jars()):
            return f"The SourceAFIS jars were not found in {self.jar_dir} (build the image with the fingerprint stage)."
        import shutil

        if not (os.getenv("JAVA_HOME") or shutil.which("java")):
            return "No Java runtime found: build the image with INSTALL_JVM=true."
        return self._error

    def _ensure(self) -> dict:
        with self._lock:
            if self._cls is not None:
                return self._cls
            reason = self.not_ready_reason()
            if reason:
                raise NotImplementedError(f"SourceAFIS JVM engine unavailable: {reason}")
            import jpype

            try:
                if not jpype.isJVMStarted():
                    jpype.startJVM("-Xmx512m", "-Djava.awt.headless=true", classpath=self._jars(), convertStrings=False)
                pkg = "com.machinezoo.sourceafis."
                self._cls = {n: jpype.JClass(pkg + n) for n in ("FingerprintImage", "FingerprintImageOptions", "FingerprintTemplate", "FingerprintMatcher")}
                self._cls["ObjectMapper"] = jpype.JClass("com.fasterxml.jackson.databind.ObjectMapper")
                self._cls["CBORFactory"] = jpype.JClass("com.fasterxml.jackson.dataformat.cbor.CBORFactory")
            except Exception as exc:
                self._error = f"The JVM or the SourceAFIS classes could not be started: {exc}"
                raise NotImplementedError(f"SourceAFIS JVM engine unavailable: {self._error}") from exc
            return self._cls

    @staticmethod
    def _jbytes(data: bytes):
        """A real Java byte[]: without this JPype may pick a String overload (e.g. Jackson's readTree) for Python bytes."""
        import jpype

        return jpype.JArray(jpype.JByte)(data)

    # -- engine protocol -----------------------------------------------------
    def precheck(self, image_bytes: bytes) -> dict:
        """The numpy quality gate, run before the JVM is involved at all."""
        try:
            import cv2
            import numpy as np

            gray = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:
            gray = None
        if gray is None:
            return {"passed": False, "quality_score": 0.0, "nfiq_score": None, "method": GATE_METHOD, "reason": "The image could not be decoded."}
        return gray_quality(gray, self.dpi)

    def extract(self, image_bytes: bytes) -> Template:
        c = self._ensure()
        try:
            options = c["FingerprintImageOptions"]().dpi(self.dpi)
            image = c["FingerprintImage"](self._jbytes(image_bytes), options)
            native = c["FingerprintTemplate"](image)
            data = bytes(native.toByteArray())
            minutiae = int(c["ObjectMapper"](c["CBORFactory"]()).readTree(self._jbytes(data)).get("positionsX").size())
        except Exception as exc:          # unreadable / unsupported image or an empty template
            raise LowQualityError(f"SourceAFIS could not extract a template from this image: {exc}", 0.0) from exc
        return Template(data, minutiae, native)

    def score(self, probe: Template, candidate: Template) -> float:
        c = self._ensure()
        if probe.native is None:
            probe.native = c["FingerprintTemplate"](self._jbytes(probe.data))
        matcher = getattr(probe, "_matcher", None)
        if matcher is None:
            matcher = probe._matcher = c["FingerprintMatcher"](probe.native)        # the probe is indexed once, then compared with all
        if candidate.native is None:
            candidate.native = c["FingerprintTemplate"](self._jbytes(candidate.data))
        return float(matcher.match(candidate.native))
