"""Fingerprint index and endpoints, with a synthetic engine (no real matcher is installed: SourceAFIS has no PyPI package).

A test "print" is a JPEG-magic byte string carrying a JSON list of minutiae, so the whole path (quality gate, scoring,
persistence, scoping, roles) is exercised without image processing.
"""

import base64
import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import fingerprint
import platform_api as pa
from fingerprint import FingerprintIndex, LowQualityError, Template
from tests.unit.test_platform_critical_path import _auth_headers

KEY = fingerprint.derive_key("unit-test-secret-unit-test-secret")


def print_image(minutiae, blur=False) -> bytes:
    return b"\xff\xd8\xff" + json.dumps({"m": minutiae, "blur": blur}).encode()


def pts(seed, n=20):
    return [[seed * 100 + i, i * 3 % 50, (i * 17) % 360] for i in range(n)]


class SyntheticEngine:
    name = "synthetic"

    def extract(self, image_bytes):
        doc = json.loads(image_bytes[3:])
        if doc["blur"]:
            raise LowQualityError("image too blurry", quality_score=4.0)
        return Template(json.dumps(doc["m"]).encode(), len(doc["m"]))

    def score(self, probe, candidate):
        a = {tuple(x) for x in json.loads(probe.data)}
        b = {tuple(x) for x in json.loads(candidate.data)}
        return 100.0 * len(a & b) / max(len(a), len(b), 1)


class MemoryStore:
    """Stands in for the MinIO bucket; the etag changes on every write, like an object store's."""
    def __init__(self):
        self.blob, self.version = None, 0

    def etag(self):
        return None if self.blob is None else f"v{self.version}"

    def get(self):
        return self.blob

    def put(self, data):
        self.blob, self.version = data, self.version + 1


def make_index(store=None, engine=None):
    return FingerprintIndex(store or MemoryStore(), engine or SyntheticEngine(), key=KEY)


# ---------------------------------------------------------------------------
# Index behaviour
# ---------------------------------------------------------------------------

def test_low_quality_image_returns_quality_error():
    idx = make_index()
    out = idx.match(print_image(pts(1), blur=True))          # the engine raises: must not become a 500
    assert out["quality_too_low"] is True and out["quality_score"] == 4.0 and out["hits"] == []
    sparse = idx.match(print_image(pts(1, n=5)))              # too few minutiae to search
    assert sparse["quality_too_low"] and sparse["quality"]["passed"] is False and sparse["nfiq_score"] is None
    with pytest.raises(LowQualityError):
        idx.enroll("X", "FIR-1", print_image(pts(1), blur=True))


def test_enroll_and_match_roundtrip_and_persistence():
    store = MemoryStore()
    a = make_index(store)
    sid = a.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1)), jurisdiction="Central District")
    a.enroll("Suresh Patel", "FIR-2", print_image(pts(2)), jurisdiction="Central District")
    partial = pts(1)[:14] + pts(9, 6)                          # a latent overlapping 14 of 20 minutiae
    b = make_index(store)                                      # a second worker sees the same persisted index
    out = b.match(print_image(partial))
    top = out["hits"][0]
    assert top["suspect_id"] == sid and top["name"] == "Ramesh Kumar" and top["rank"] == 1
    assert top["score"] == 70.0 and out["hits"][1]["score"] == 0.0
    assert b.match(print_image(pts(1)))["hits"][0]["score"] == 100.0


def test_match_below_threshold_returns_no_match():
    idx = make_index()
    idx.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1)))
    hits = idx.match(print_image(pts(1)[:6] + pts(7, 14)))["hits"]   # only 6 of 20 minutiae in common -> 30
    assert hits[0]["score"] == 30.0 < idx.threshold
    assert fingerprint.confidence_label(30.0) == "Low / insufficient"
    assert [fingerprint.confidence_label(s) for s in (40, 69.9, 70, 100)] == ["Medium", "Medium", "High", "High"]
    # a latent probe says so in its label, so the officer reading "Medium" knows where it came from
    assert [fingerprint.confidence_label(s, "latent") for s in (45, 70, 30)] == ["Medium / latent match", "High / latent match", "Low / insufficient"]


def test_tombstone_delete_removes_the_template_but_keeps_the_record():
    store = MemoryStore()
    idx = make_index(store)
    idx.enroll("A", "FIR-1", print_image(pts(1)))
    idx.enroll("B", "FIR-2", print_image(pts(2)))
    assert idx.delete_by_fir("FIR-1") == 1 and idx.delete_by_fir("FIR-1") == 0
    assert idx.count() == 1 and idx.match(print_image(pts(1)))["hits"][0]["name"] == "B"
    stored = json.loads(json.loads(store.blob)["payload"])["entries"]
    tomb = next(e for e in stored if e["fir_id"] == "FIR-1")
    assert tomb["deleted"] is True and tomb["template"] is None       # biometric data gone, audit trail kept


def test_scope_limits_which_prints_are_searched():
    idx = make_index()
    idx.enroll("Central Person", "F1", print_image(pts(1)), jurisdiction="Central District")
    idx.enroll("West Person", "F2", print_image(pts(1)), jurisdiction="West Range")
    names = lambda scope: {h["name"] for h in idx.match(print_image(pts(1)), scope=scope)["hits"]}
    assert names("Central District") == {"Central Person"} and names(None) == {"Central Person", "West Person"}
    assert idx.count("West Range") == 1


def test_tampered_or_malformed_index_is_refused():
    store = MemoryStore()
    idx = make_index(store)
    idx.enroll("A", "FIR-1", print_image(pts(1)))
    env = json.loads(store.blob)
    payload = json.loads(env["payload"])
    payload["entries"].append({"suspect_id": "evil", "name": "Injected", "fir_id": "X", "template": base64.b64encode(b"[]").decode(), "deleted": False})
    env["payload"] = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    store.blob, store.version = json.dumps(env).encode(), store.version + 1        # attacker edits the bucket
    with pytest.raises(fingerprint.IndexIntegrityError):
        make_index(store).count()
    store.blob, store.version = b"not json", store.version + 1
    with pytest.raises(fingerprint.IndexIntegrityError):
        make_index(store).count()
    other_key = FingerprintIndex(MemoryStore(), SyntheticEngine(), key=fingerprint.derive_key("a different secret entirely!!!!!!"))
    other_key.store.blob, other_key.store.version = json.dumps(json.loads(idx._seal([]))).encode(), 1
    assert make_index(other_key.store).count() == 0                                # same key: an empty index is valid


def test_unavailable_engine_explains_itself_and_is_the_default():
    assert isinstance(fingerprint.load_engine(""), fingerprint.UnavailableEngine)
    assert isinstance(fingerprint.load_engine("no.such.module:factory"), fingerprint.UnavailableEngine)   # bad config must not crash
    with pytest.raises(NotImplementedError, match="SourceAFIS has no PyPI package"):
        fingerprint.UnavailableEngine().extract(b"x")


def test_bulk_zip_parses_filename_convention():
    p = fingerprint.parse_zip_member_name
    assert p("Ramesh_Kumar__FIR-2026-001.jpg") == ("Ramesh Kumar", "FIR-2026-001")
    assert p("prints/sub/Asha__FIR_7.PNG") == ("Asha", "FIR_7")
    assert p("A__B__C.jpeg") == ("A B", "C")                       # splits on the LAST double underscore
    assert p("no_convention.jpg") is None and p("Name__FIR.txt") is None and p("__FIR.jpg") is None and p("Name__.jpg") is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    import main
    idx = make_index()
    monkeypatch.setattr(main, "fingerprint_index", idx)
    monkeypatch.setattr(main, "_fp_fir_jurisdiction", lambda fir: "Test")            # tokens use jurisdiction "Test"
    monkeypatch.setattr(main, "log_action", MagicMock())
    monkeypatch.setattr(main, "_authorize_case", MagicMock())
    return TestClient(main.app), idx, main


def enroll(client, role, name="Ramesh Kumar", fir="FIR-1", image=None):
    return client.post("/api/v1/biometric/fingerprint/enroll", data={"name": name, "fir_id": fir},
                       files={"file": ("p.jpg", print_image(pts(1)) if image is None else image, "image/jpeg")}, headers=_auth_headers(role))


def test_supervisor_only_enroll_enforced(api):
    client, idx, main = api
    for role in ("investigator", "analyst"):
        assert enroll(client, role).status_code == 403
    assert idx.count() == 0
    r = enroll(client, "supervisor")
    assert r.status_code == 200 and idx.count() == 1
    body = r.json()
    assert {"suspect_id", "name", "fir_id", "enrolled_at", "message"} <= set(body) and "verify before use" in body["message"]
    assert enroll(client, "admin", name="B", fir="FIR-2", image=print_image(pts(2))).status_code == 200
    call = main.log_action.call_args_list[0]
    assert call.kwargs["action"] == "fingerprint_enroll" and "image_sha256" in call.kwargs["extra"]


def test_enroll_rejects_bad_input_and_low_quality(api):
    client, idx, _ = api
    assert enroll(client, "supervisor", image=b"MZ not an image").status_code == 415
    assert enroll(client, "supervisor", image=b"").status_code in (400, 422)
    r = enroll(client, "supervisor", image=print_image(pts(1), blur=True))
    assert r.status_code == 422 and r.json()["detail"]["quality_too_low"] is True and r.json()["detail"]["nfiq_score"] is None
    assert idx.count() == 0


def match(client, role, image, **form):
    return client.post("/api/v1/biometric/fingerprint/match", data=form, files={"file": ("q.jpg", image, "image/jpeg")}, headers=_auth_headers(role))


def test_match_endpoint_shape_scoring_and_audit(api):
    client, idx, main = api
    idx.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1)), jurisdiction="Test")
    idx.enroll("Suresh Patel", "FIR-2", print_image(pts(1)[:8] + pts(3, 12)), jurisdiction="Test")
    r = match(client, "investigator", print_image(pts(1)))
    body = r.json()
    assert r.status_code == 200 and body["match_found"] is True and body["quality_check"]["passed"] is True
    assert body["quality_check"]["nfiq_score"] is None and body["quality_check"]["method"] == "minutiae-count"
    assert body["candidates"][0] == {"rank": 1, "name": "Ramesh Kumar", "fir_id": "FIR-1", "score": 100.0, "confidence_label": "High"}
    assert all(c["score"] >= 40 for c in body["candidates"]) and len(body["candidates"]) <= 5
    assert "forensic lab confirmation" in body["disclaimer"] and "verify before use" in body["disclaimer"]
    assert "not comparable with scores from other AFIS" in body["disclaimer"] and "not been validated for latent" in body["disclaimer"]
    assert body["print_type"] == "rolled"
    extra = main.log_action.call_args.kwargs["extra"]
    assert main.log_action.call_args.kwargs["action"] == "fingerprint_match" and extra["top_candidate"] == "Ramesh Kumar" and extra["match_found"]


def test_match_endpoint_no_match_low_quality_scope_and_case(api):
    client, idx, main = api
    idx.enroll("Elsewhere Person", "FIR-9", print_image(pts(1)), jurisdiction="West Range")
    r = match(client, "investigator", print_image(pts(1)))                       # investigator is scoped to "Test"
    assert r.status_code == 200 and r.json()["match_found"] is False and r.json()["candidates"] == []
    assert match(client, "admin", print_image(pts(1))).json()["match_found"] is True     # unscoped role sees it
    r = match(client, "investigator", print_image(pts(1), blur=True))              # a print that is too poor to search is refused with 422
    detail = r.json()["detail"]
    assert r.status_code == 422 and detail["quality_too_low"] is True and detail["nfiq_score"] is None and detail["quality_check"]["passed"] is False
    assert main.log_action.call_args.kwargs["extra"]["match_found"] is False
    match(client, "investigator", print_image(pts(1)), case_id="C-1", justification="lead")
    main._authorize_case.assert_called_once()
    assert match(client, "investigator", b"plain text").status_code == 415


def test_unavailable_engine_is_501_and_status_says_so(monkeypatch):
    import main
    monkeypatch.setattr(main, "fingerprint_index", FingerprintIndex(MemoryStore(), fingerprint.UnavailableEngine(), key=KEY))
    monkeypatch.setattr(main, "log_action", MagicMock())
    client = TestClient(main.app)
    r = match(client, "investigator", b"\xff\xd8\xffJPEGDATA")
    assert r.status_code == 501 and "SourceAFIS has no PyPI package" in r.json()["detail"]
    s = client.get("/api/v1/biometric/fingerprint/status", headers=_auth_headers("investigator")).json()
    assert s["available"] is False and s["engine"] == "unavailable" and s["enrolled"] == 0 and s["match_threshold"] == 40


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_bulk_zip_endpoint_enrols_reports_failures_and_enforces_role(api):
    client, idx, main = api
    archive = make_zip({
        "Ramesh_Kumar__FIR-1.jpg": print_image(pts(1)),
        "Suresh_Patel__FIR-2.jpg": print_image(pts(2)),
        "Blurry_Person__FIR-3.jpg": print_image(pts(3), blur=True),
        "badname.jpg": print_image(pts(4)),
        "Not_Image__FIR-5.jpg": b"this is not an image",
        "__MACOSX/junk__FIR-6.jpg": print_image(pts(6)),
        "../../evil__FIR-7.jpg": print_image(pts(7)),                 # path traversal: only the base name is used, nothing written to disk
    })
    post = lambda role: client.post("/api/v1/biometric/fingerprint/bulk-enroll-zip", files={"file": ("p.zip", archive, "application/zip")}, headers=_auth_headers(role))
    assert post("investigator").status_code == 403
    body = post("supervisor").json()
    assert body["enrolled"] == 3 and body["failed"] == 3                     # Ramesh, Suresh, evil(basename) in; blurry, badname, not-image out
    assert {e["file"] for e in body["errors"]} == {"Blurry_Person__FIR-3.jpg", "badname.jpg", "Not_Image__FIR-5.jpg"}
    assert idx.count() == 3
    bad = client.post("/api/v1/biometric/fingerprint/bulk-enroll-zip", files={"file": ("p.zip", b"nope", "application/zip")}, headers=_auth_headers("admin"))
    assert bad.status_code == 400
    assert main.log_action.call_args_list[-1].kwargs["action"] == "fingerprint_bulk_enroll"


# ---------------------------------------------------------------------------
# print_type, the quality gate, and the JVM engine's readiness logic
# ---------------------------------------------------------------------------

def test_latent_probe_gets_latent_labels_and_print_type_is_validated(api):
    client, idx, main = api
    idx.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1)), jurisdiction="Test")
    partial = print_image(pts(1)[:9] + pts(8, 11))                                  # 9 of 20 shared -> 45
    r = match(client, "investigator", partial, print_type="latent").json()
    assert r["print_type"] == "latent" and r["candidates"][0]["score"] == 45.0
    assert r["candidates"][0]["confidence_label"] == "Medium / latent match"
    assert match(client, "investigator", partial, print_type="rolled").json()["candidates"][0]["confidence_label"] == "Medium"
    assert main.log_action.call_args.kwargs["extra"]["print_type"] == "rolled"
    assert match(client, "investigator", partial, print_type="wet-ink").status_code == 422


class GatedEngine(SyntheticEngine):
    """An engine with a proactive image gate; counts how often extraction (the expensive step) actually runs."""
    name = "gated"

    def __init__(self):
        self.extracted = 0

    def precheck(self, image_bytes):
        ok = json.loads(image_bytes[3:]).get("q", 100) >= 30
        return {"passed": ok, "quality_score": float(json.loads(image_bytes[3:]).get("q", 100)), "nfiq_score": None,
                "method": "variance+ridge-frequency", "reason": None if ok else "Print quality too low: test gate"}

    def extract(self, image_bytes):
        self.extracted += 1
        return super().extract(image_bytes)


def gated_print(q, n=20):
    return b"\xff\xd8\xff" + json.dumps({"m": pts(1, n), "blur": False, "q": q}).encode()


def test_quality_gate_runs_before_extraction_and_matching():
    engine = GatedEngine()
    idx = make_index(engine=engine)
    idx.enroll("A", "F1", gated_print(90))
    engine.extracted = 0
    out = idx.match(gated_print(10))
    assert out["quality_too_low"] and out["hits"] == [] and engine.extracted == 0      # the matcher never ran
    assert out["quality"]["nfiq_score"] is None and out["quality"]["method"] == "variance+ridge-frequency" and "test gate" in out["message"]
    ok = idx.match(gated_print(90))
    assert ok["quality_too_low"] is False and engine.extracted == 1
    assert ok["quality"]["method"] == "min(variance+ridge-frequency, minutiae-count)"
    assert ok["quality"]["quality_score"] == min(90.0, 100 * 20 / fingerprint.FULL_QUALITY_MINUTIAE)      # the weaker of the two measures
    with pytest.raises(LowQualityError, match="test gate"):
        idx.enroll("B", "F2", gated_print(10))
    assert engine.extracted == 1                                                       # enrolment also refused before extraction


def ridge_image(np, size=128, period=9.0, angle=0.6, curve=0.0004, amp=90.0):
    y, x = np.mgrid[0:size, 0:size].astype(float)
    phase = 2 * np.pi * (x * np.cos(angle) + y * np.sin(angle)) / period + curve * ((x - size / 2) ** 2 + (y - size / 2) ** 2)
    return 128 + amp * np.sin(phase)


def test_gray_quality_separates_clean_ridges_from_blank_noise_and_tiny_images():
    from tests.unit.real_libs import real_libs

    with real_libs():
        import numpy as np

        rng = np.random.default_rng(7)
        clean = fingerprint.gray_quality(ridge_image(np))
        assert clean["passed"] and clean["quality_score"] > 60 and clean["nfiq_score"] is None and clean["method"] == "variance+ridge-frequency"
        fragment = fingerprint.gray_quality(ridge_image(np, size=64))                       # a small but clean latent-like fragment still passes
        assert fragment["passed"] and fragment["valid_blocks"] >= fingerprint.GATE_MIN_VALID_BLOCKS
        blank = fingerprint.gray_quality(np.full((128, 128), 200.0))
        assert not blank["passed"] and blank["quality_score"] == 0 and "blank" in blank["reason"]
        noise = fingerprint.gray_quality(rng.uniform(0, 255, (128, 128)))
        assert not noise["passed"] and noise["quality_score"] < 30
        wrong_scale = fingerprint.gray_quality(ridge_image(np, period=2.5))                 # far finer than any ridge period
        assert not wrong_scale["passed"]
        faint = fingerprint.gray_quality(ridge_image(np, amp=8.0))                          # right pattern, washed out (contrast ~6)
        assert not faint["passed"] and faint["contrast"] < fingerprint.GATE_MIN_CONTRAST and "faint or blurred" in faint["reason"]
        assert faint["quality_score"] <= 39.0                                                # a refused print never shows a healthy-looking score
        assert clean["contrast"] > 50 and fragment["contrast"] > 50
        tiny = fingerprint.gray_quality(ridge_image(np, size=48))
        assert not tiny["passed"] and "too small" in tiny["reason"]
        # DPI scaling: real ridges are about twice as wide in pixels at 1000 DPI, so 6 px ridges are plausible at 500 DPI only
        fine = ridge_image(np, period=6.0)
        assert fingerprint.gray_quality(fine, dpi=500)["passed"] and not fingerprint.gray_quality(fine, dpi=1000)["passed"]


def test_jvm_engine_reports_why_it_is_not_ready_without_starting_a_jvm(tmp_path):
    engine = fingerprint.JvmSourceAFISEngine(jar_dir=str(tmp_path / "nope"))
    reason = engine.not_ready_reason()
    assert reason and ("jpype1" in reason or "jars were not found" in reason)
    with pytest.raises(NotImplementedError, match="SourceAFIS JVM engine unavailable"):
        engine.extract(b"\xff\xd8\xff")                                                    # -> API 501, never a 500
    idx = FingerprintIndex(MemoryStore(), engine, key=KEY)
    status = idx.engine_status()
    assert status["available"] is False and status["message"] == reason


def test_engine_factory_can_load_the_jvm_engine_by_name():
    engine = fingerprint.load_engine("fingerprint:JvmSourceAFISEngine")
    assert isinstance(engine, fingerprint.JvmSourceAFISEngine) and engine.name == "sourceafis-jvm"      # constructing it must not start a JVM
    assert isinstance(fingerprint.load_engine("fingerprint:NoSuchEngine"), fingerprint.UnavailableEngine)


def test_status_endpoint_reports_engine_readiness(monkeypatch):
    import main

    engine = fingerprint.JvmSourceAFISEngine(jar_dir="/nonexistent")
    monkeypatch.setattr(main, "fingerprint_index", FingerprintIndex(MemoryStore(), engine, key=KEY))
    s = TestClient(main.app).get("/api/v1/biometric/fingerprint/status", headers=_auth_headers("investigator")).json()
    assert s["engine"] == "sourceafis-jvm" and s["available"] is False and s["message"]


# ---------------------------------------------------------------------------
# Parallel scoring and the latent minutiae floor
# ---------------------------------------------------------------------------

def test_parallel_scoring_ranks_exactly_like_the_serial_scan(monkeypatch):
    idx = make_index()
    entries = []
    for i in range(500):                                   # enough to cross PARALLEL_MIN_CANDIDATES; many equal scores on purpose
        tpl = Template(json.dumps(pts(i % 7, 20)).encode(), 20)
        entries.append({"suspect_id": str(i), "name": f"P{i}", "fir_id": f"F{i}", "jurisdiction": "J", "enrolled_at": "x",
                        "minutiae": 20, "template": base64.b64encode(tpl.data).decode(), "deleted": False})
    idx.store.put(idx._seal(entries))
    probe = print_image(pts(3, 20))
    monkeypatch.setattr(fingerprint, "MATCH_THREADS", 1)
    serial = idx.match(probe, top_k=500)["hits"]
    monkeypatch.setattr(fingerprint, "MATCH_THREADS", 4)
    parallel = idx.match(probe, top_k=500)["hits"]
    assert [(h["suspect_id"], h["score"]) for h in serial] == [(h["suspect_id"], h["score"]) for h in parallel]
    assert serial[0]["score"] == 100.0 and len(serial) == 500
    ties = [h["suspect_id"] for h in parallel if h["score"] == 100.0]
    assert ties == sorted(ties, key=int)                    # equal scores keep enrolment order, so results are reproducible


def test_latent_probe_gets_the_lower_minutiae_floor_but_enrolment_does_not():
    idx = make_index()
    idx.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1, 20)))
    nine = print_image(pts(1, 9))                            # a door-handle partial: 9 minutiae
    rolled = idx.match(nine, print_type="rolled")
    assert rolled["quality_too_low"] and rolled["quality"]["minimum_minutiae"] == fingerprint.MIN_MINUTIAE == 12
    latent = idx.match(nine, print_type="latent")
    assert latent["quality_too_low"] is False and latent["quality"]["minimum_minutiae"] == fingerprint.MIN_MINUTIAE_LATENT == 8
    assert latent["hits"][0]["name"] == "Ramesh Kumar"
    assert idx.match(print_image(pts(1, 7)), print_type="latent")["quality_too_low"]        # still not unlimited: below 8 is refused
    with pytest.raises(LowQualityError):                                                    # enrolling a 9-minutiae print is refused: the floor is not loosened for the index
        idx.enroll("X", "FIR-2", nine)


def test_endpoint_reports_the_floor_that_applied(api):
    client, idx, _ = api
    idx.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1, 20)), jurisdiction="Test")
    nine = print_image(pts(1, 9))
    r = match(client, "investigator", nine, print_type="rolled")
    assert r.status_code == 422 and r.json()["detail"]["quality_check"]["minimum_minutiae"] == 12
    r = match(client, "investigator", nine, print_type="latent")
    assert r.status_code == 200 and r.json()["quality_check"]["minimum_minutiae"] == 8
    assert r.json()["candidates"][0]["name"] == "Ramesh Kumar"
