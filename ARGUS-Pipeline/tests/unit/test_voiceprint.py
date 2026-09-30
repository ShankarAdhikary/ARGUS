"""Voice biometrics: authorization reference, ranking-only vs calibrated mode, index integrity, endpoints, evaluation maths.

A fake engine/decoder stands in for ffmpeg + ECAPA: a test "recording" is a WAV-header-prefixed JSON document describing a
speaker, and the fake engine maps a speaker to a vector. Real numpy is swapped in (conftest stubs it).
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fingerprint
import platform_api as pa
import voiceprint
from tests.unit.real_libs import real_libs
from tests.unit.test_platform_critical_path import _auth_headers

ROOT = Path(__file__).resolve().parents[2]
KEY = fingerprint.derive_key("unit-test-secret-unit-test-secret", voiceprint.KEY_PURPOSE)
REF = "LI/MHA/2026/00417"


@pytest.fixture(autouse=True)
def np_real():
    with real_libs(scipy=False):
        import numpy as np

        yield np


class MemoryStore:
    def __init__(self):
        self.blob, self.version = None, 0

    def etag(self):
        return None if self.blob is None else f"v{self.version}"

    def get(self):
        return self.blob

    def put(self, data):
        self.blob, self.version = data, self.version + 1


def recording(speaker, seconds=10.0, noise=0.01) -> bytes:
    return b"RIFF\x00\x00\x00\x00WAVE" + json.dumps({"spk": speaker, "seconds": seconds, "noise": noise}).encode()


def fake_decode(audio, suffix=".wav"):
    """Speech-like signal whose pitch encodes the speaker: 0.4 s tone bursts separated by 0.2 s of low noise."""
    import numpy as np

    doc = json.loads(audio[12:])
    sr = voiceprint.SAMPLE_RATE
    rng = np.random.default_rng(1)
    pieces, t = [], 0.0
    freq = 180.0 + 90.0 * doc["spk"]
    while t < doc["seconds"]:
        n = int(0.4 * sr)
        tt = np.arange(n) / sr
        # pitch tone (the speaker) plus a broadband, fricative-like component so the sample is wideband like real speech
        pieces.append((0.3 * np.sin(2 * np.pi * freq * tt) + 0.08 * rng.normal(0, 1, n)) * np.hanning(n) + rng.normal(0, doc["noise"], n))
        pieces.append(rng.normal(0, 0.002, int(0.2 * sr)))
        t += 0.6
    return np.concatenate(pieces).astype(np.float32)


class FakeEngine:
    name, model_id = "fake", "fake/ecapa"

    def embed(self, wave):
        import numpy as np

        spectrum = np.abs(np.fft.rfft(wave))
        pitch = np.fft.rfftfreq(len(wave), 1 / voiceprint.SAMPLE_RATE)[int(np.argmax(spectrum))]
        rng = np.random.default_rng(int(round(pitch / 40)))            # same speaker (pitch bucket) -> same base vector
        vec = rng.normal(size=voiceprint.EMBEDDING_DIM).astype(np.float32)
        vec += np.random.default_rng(int(abs(wave[:64].sum() * 1e6)) % 2**31).normal(0, 0.05, voiceprint.EMBEDDING_DIM).astype(np.float32)
        return vec / np.linalg.norm(vec)


def make_index(calibration=None, store=None):
    return voiceprint.VoiceprintIndex(store or MemoryStore(), FakeEngine(), key=KEY, calibration=calibration, decoder=fake_decode)


def calibration(far_0_1=0.6, far_0_01=0.8):
    return voiceprint.Calibration("fake/ecapa", "2026-09-30", {"eer": 0.4, "far_1pct": 0.5, "far_0_1pct": far_0_1, "far_0_01pct": far_0_01})


# ---------------------------------------------------------------------------
# Authorization reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", ["LI/MHA/2026/00417", "Order No. 44-B (2026)", "IT-ACT-69/2026/117", "  spaced   ref 99  "])
def test_real_looking_references_are_accepted_and_normalised(ref):
    assert voiceprint.validate_lawful_interception_ref(ref) == " ".join(ref.split())


@pytest.mark.parametrize("ref", [None, "", "  ", "ab", "NA", "n/a", "None", "TBD", "test", "-", "xxx", "0000", "aaaa", "123", "pending",
                                  "<script>alert(1)</script>", "ref;drop table", "x" * 101])
def test_blank_placeholder_and_junk_references_are_rejected(ref):
    with pytest.raises(ValueError, match="lawful_interception_ref"):
        voiceprint.validate_lawful_interception_ref(ref)


# ---------------------------------------------------------------------------
# Channel quality
# ---------------------------------------------------------------------------

def test_channel_quality_separates_speech_from_silence_and_flags_telephone_audio(np_real):
    np = np_real
    good = voiceprint.channel_quality(fake_decode(recording(0, seconds=12)))
    assert good["passed"] and good["net_speech_seconds"] > 3 and good["snr_db"] > 15 and good["quality_score"] > 40 and not good["narrowband"]
    silence = voiceprint.channel_quality(np.random.default_rng(0).normal(0, 0.001, 16000 * 8).astype(np.float32))
    assert not silence["passed"] and silence["quality_score"] < 30
    short = voiceprint.channel_quality(fake_decode(recording(0, seconds=2.0)))
    assert not short["passed"] and "Not enough speech" in short["reason"]
    assert not voiceprint.channel_quality(np.zeros(10, np.float32))["passed"]
    # telephone channel: speech-like bursts with everything above ~3.4 kHz removed -> flagged narrowband
    wave = fake_decode(recording(0, seconds=12))
    spec = np.fft.rfft(wave); freqs = np.fft.rfftfreq(len(wave), 1 / 16000); spec[freqs > 3400] = 0
    phone = voiceprint.channel_quality(np.fft.irfft(spec, n=len(wave)).astype(np.float32))
    assert phone["narrowband"] and phone["bandwidth_hz"] < 4200 and phone["passed"] and good["bandwidth_hz"] > phone["bandwidth_hz"]
    clipped = voiceprint.channel_quality(np.clip(fake_decode(recording(0)) * 8, -1, 1))
    assert clipped["clipping_ratio"] > 0.05 and clipped["quality_score"] < good["quality_score"]


# ---------------------------------------------------------------------------
# Index: ranking-only vs calibrated
# ---------------------------------------------------------------------------

def test_uncalibrated_mode_ranks_but_makes_no_decision():
    idx = make_index()
    idx.enroll("Ramesh Kumar", "FIR-1", recording(0), REF, jurisdiction="Central District")
    idx.enroll("Suresh Patel", "FIR-2", recording(3), REF, jurisdiction="Central District")
    out = idx.match(recording(0, noise=0.03), REF)
    assert out["calibrated"] is False and out["match_found"] is None and out["quality_too_low"] is False
    assert [c["name"] for c in out["candidates"]] == ["Ramesh Kumar", "Suresh Patel"]           # ranked, all listed (no threshold to cut at)
    assert out["candidates"][0]["score"] > out["candidates"][1]["score"] and out["candidates"][0]["rank"] == 1
    assert {c["confidence_label"] for c in out["candidates"]} == {voiceprint.UNCALIBRATED_LABEL}


def test_calibrated_mode_lists_only_above_the_measured_threshold_with_measured_labels():
    idx = make_index(calibration(far_0_1=0.6, far_0_01=0.97))
    idx.enroll("Ramesh Kumar", "FIR-1", recording(0), REF)
    idx.enroll("Suresh Patel", "FIR-2", recording(3), REF)
    out = idx.match(recording(0, noise=0.03), REF)
    assert out["calibrated"] and out["match_found"] is True
    assert [c["name"] for c in out["candidates"]] == ["Ramesh Kumar"]                             # Suresh scores below the threshold: not listed
    assert out["candidates"][0]["confidence_label"] in ("High", "Medium")
    none = idx.match(recording(2), REF)
    assert none["match_found"] is False and none["candidates"] == []
    strict = make_index(calibration(far_0_1=0.99999, far_0_01=None))
    strict.enroll("Ramesh Kumar", "FIR-1", recording(0), REF)
    assert strict.match(recording(0, noise=0.05), REF)["match_found"] is False                    # a stricter measured threshold lists nothing
    assert voiceprint.confidence_label(0.9, calibration(0.6, None)) == "Medium"                   # no 0.01% threshold measured: never "High"
    assert voiceprint.confidence_label(0.9, calibration(0.6, 0.85)) == "High"
    assert voiceprint.confidence_label(0.1, calibration(0.6, 0.85)) == "Below threshold"


def test_enrolment_and_match_both_require_a_real_authorization_reference():
    idx = make_index()
    for bad in ("", "n/a", "test"):
        with pytest.raises(ValueError, match="lawful_interception_ref"):
            idx.enroll("X", "F", recording(0), bad)
        with pytest.raises(ValueError, match="lawful_interception_ref"):
            idx.match(recording(0), bad)
    assert idx.count() == 0                                                                        # nothing was enrolled by the rejected calls


def test_entry_holds_the_reference_hash_duration_and_quality_but_no_audio():
    store = MemoryStore()
    idx = make_index(store=store)
    audio = recording(1, seconds=9)
    result = idx.enroll("Ramesh Kumar", "FIR-1", audio, REF, jurisdiction="Central District")
    assert result["lawful_interception_ref"] == REF and result["sample_sha256"] == __import__("hashlib").sha256(audio).hexdigest()
    stored = json.loads(json.loads(store.blob)["payload"])["entries"][0]
    assert stored["lawful_interception_ref"] == REF and stored["duration_seconds"] > 8 and stored["net_speech_seconds"] > 3
    assert stored["channel_quality_score"] > 0 and stored["jurisdiction"] == "Central District" and stored["model_id"] == "fake/ecapa"
    assert len(base64.b64decode(stored["embedding"])) == voiceprint.EMBEDDING_DIM * 4               # 192 float32s, nothing else
    assert "audio" not in stored and "wave" not in stored


def test_poor_audio_is_refused_not_matched_or_enrolled():
    idx = make_index()
    with pytest.raises(voiceprint.LowQualityAudio, match="Not enough speech"):
        idx.enroll("X", "F", recording(0, seconds=1.5), REF)
    out = idx.match(recording(0, seconds=1.5), REF)
    assert out["quality_too_low"] and out["candidates"] == [] and "Not enough speech" in out["message"]
    assert idx.count() == 0


def test_scope_and_tombstones():
    idx = make_index()
    idx.enroll("Central", "F1", recording(0), REF, jurisdiction="Central District")
    idx.enroll("West", "F2", recording(0), REF, jurisdiction="West Range")
    names = lambda scope: {c["name"] for c in idx.match(recording(0), REF, scope=scope)["candidates"]}
    assert names("Central District") == {"Central"} and names(None) == {"Central", "West"} and idx.count("West Range") == 1
    assert idx.delete_by_fir("F1") == 1 and idx.delete_by_fir("F1") == 0
    assert names(None) == {"West"}
    doc = json.loads(json.loads(idx.store.blob)["payload"])["entries"]
    tomb = next(e for e in doc if e["fir_id"] == "F1")
    assert tomb["deleted"] and tomb["embedding"] is None and tomb["lawful_interception_ref"] == REF     # biometric gone, authorization record kept


def test_tampered_index_is_refused_and_keys_are_not_shared_with_the_fingerprint_index():
    store = MemoryStore()
    idx = make_index(store=store)
    idx.enroll("A", "F1", recording(0), REF)
    env = json.loads(store.blob)
    payload = json.loads(env["payload"])
    payload["entries"].append({**payload["entries"][0], "suspect_id": "evil", "name": "Injected"})
    env["payload"] = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    store.blob, store.version = json.dumps(env).encode(), store.version + 1
    with pytest.raises(voiceprint.IndexIntegrityError):
        make_index(store=store).count()
    assert fingerprint.derive_key("s") != fingerprint.derive_key("s", voiceprint.KEY_PURPOSE)         # a sealed fingerprint index will not verify here
    other = make_index(store=MemoryStore())
    other.store.blob, other.store.version = idx.__class__(other.store, FakeEngine(), key=fingerprint.derive_key("s"), decoder=fake_decode)._seal([]), 1
    with pytest.raises(voiceprint.IndexIntegrityError):
        other.count()


# ---------------------------------------------------------------------------
# Calibration file
# ---------------------------------------------------------------------------

def good_counts():
    cells = {f"{l}|{c}": 20 for l in ("hi", "en", "bn") for c in ("phone", "direct")}
    return {"speakers": 60, "languages": ["bn", "en", "hi"], "channels": ["direct", "phone"], "speakers_per_cell": cells, "impostor_trials": 28000}


def write_cal(tmp_path, **overrides):
    doc = {"model_id": "fake/ecapa", "created_at": "2026-09-30", "counts": good_counts(),
           "thresholds": {"eer": 0.4, "far_1pct": 0.5, "far_0_1pct": 0.62, "far_0_01pct": None}}
    doc.update(overrides)
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(doc))
    return str(path)


def test_calibration_loads_only_when_the_evaluation_was_big_enough_and_for_this_model(tmp_path):
    cal, why = voiceprint.load_calibration(write_cal(tmp_path), "fake/ecapa")
    assert cal is not None and why is None and cal.thresholds["far_0_1pct"] == 0.62
    assert voiceprint.load_calibration(str(tmp_path / "missing.json"), "fake/ecapa")[0] is None
    assert "different" in voiceprint.load_calibration(write_cal(tmp_path), "another/model")[1] or "not 'another/model'" in voiceprint.load_calibration(write_cal(tmp_path), "another/model")[1]
    thin = good_counts(); thin["speakers"] = 12
    assert "at least 50" in voiceprint.load_calibration(write_cal(tmp_path, counts=thin), "fake/ecapa")[1]
    two_langs = good_counts(); two_langs["languages"] = ["en", "hi"]
    assert "at least 3" in voiceprint.load_calibration(write_cal(tmp_path, counts=two_langs), "fake/ecapa")[1]
    no_phone = good_counts(); no_phone["channels"] = ["direct"]
    assert "phone" in voiceprint.load_calibration(write_cal(tmp_path, counts=no_phone), "fake/ecapa")[1]
    cell = good_counts(); cell["speakers_per_cell"]["bn|phone"] = 3
    assert "bn|phone" in voiceprint.load_calibration(write_cal(tmp_path, counts=cell), "fake/ecapa")[1]
    few = good_counts(); few["impostor_trials"] = 500
    assert "impostor trials" in voiceprint.load_calibration(write_cal(tmp_path, counts=few), "fake/ecapa")[1]
    assert "0.1%" in voiceprint.load_calibration(write_cal(tmp_path, thresholds={"far_0_1pct": None}), "fake/ecapa")[1]
    (tmp_path / "junk.json").write_text("not json")
    assert "unreadable" in voiceprint.load_calibration(str(tmp_path / "junk.json"), "fake/ecapa")[1]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    import voiceprint_api

    idx = make_index()
    voiceprint_api.configure(idx)
    app = FastAPI()
    app.include_router(voiceprint_api.router)
    fake_neo = MagicMock()
    fake_neo.session.return_value.__enter__.return_value.run.return_value.single.return_value = {"j": "Test"}
    monkeypatch.setattr(pa, "_neo4j", fake_neo)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    monkeypatch.setattr(voiceprint_api, "_authorize_case", MagicMock())
    return TestClient(app), idx, voiceprint_api


def post(client, path, role, audio=None, filename="voice.wav", **form):
    return client.post(f"/api/v1/biometric/voice/{path}", data=form, headers=_auth_headers(role),
                       files={"file": (filename, recording(0) if audio is None else audio, "audio/wav")})


def test_voice_endpoints_are_not_registered_unless_enabled():
    import main

    client = TestClient(main.app)
    r = client.post("/api/v1/biometric/voice/match", data={"lawful_interception_ref": REF}, files={"file": ("a.wav", recording(0), "audio/wav")}, headers=_auth_headers("admin"))
    assert r.status_code == 404                                                                   # not 403/501: not discoverable while switched off
    assert client.get("/api/v1/biometric/voice/status", headers=_auth_headers("admin")).status_code == 404


def test_enrol_requires_supervisor_and_a_reference_and_audits_it(api):
    client, idx, _ = api
    assert post(client, "enroll", "investigator", **{"name": "N", "fir_id": "F", "lawful_interception_ref": REF}).status_code == 403
    assert post(client, "enroll", "supervisor", **{"name": "N", "fir_id": "F"}).status_code == 422                 # no reference at all
    assert pa.log_action.call_args.kwargs["action"] == "voiceprint_denied"                                        # ...and the attempt is on the audit trail
    assert pa.log_action.call_args.kwargs["extra"]["attempted"] == "enroll"
    for bad in ("n/a", "test", "ab"):
        r = post(client, "enroll", "supervisor", **{"name": "N", "fir_id": "F", "lawful_interception_ref": bad})
        assert r.status_code == 422
        assert pa.log_action.call_args.kwargs["action"] == "voiceprint_denied" and pa.log_action.call_args.kwargs["extra"]["supplied"] == bad
    assert idx.count() == 0
    assert [c.kwargs["action"] for c in pa.log_action.call_args_list].count("voiceprint_enroll") == 0            # nothing was enrolled, so no enrolment entry
    r = post(client, "enroll", "supervisor", **{"name": "Ramesh Kumar", "fir_id": "FIR-1", "lawful_interception_ref": REF})
    body = r.json()
    assert r.status_code == 200 and body["lawful_interception_ref"] == REF and body["jurisdiction"] == "Test" and "requires forensic voice expert confirmation" in body["label"]
    call = pa.log_action.call_args.kwargs
    assert call["action"] == "voiceprint_enroll" and call["extra"]["lawful_interception_ref"] == REF and REF in call["resource"]
    assert len(call["extra"]["sample_sha256"]) == 64 and "Ramesh" not in json.dumps(call)


def test_match_requires_a_reference_and_reports_ranking_only_until_calibrated(api):
    client, idx, va = api
    idx.enroll("Ramesh Kumar", "FIR-1", recording(0), REF, jurisdiction="Test")
    assert post(client, "match", "investigator").status_code == 422                                              # reference missing
    assert pa.log_action.call_args.kwargs["action"] == "voiceprint_denied" and pa.log_action.call_args.kwargs["extra"]["attempted"] == "match"
    assert post(client, "match", "investigator", lawful_interception_ref="none").status_code == 422
    r = post(client, "match", "investigator", lawful_interception_ref=REF)
    body = r.json()
    assert r.status_code == 200 and body["calibrated"] is False and body["match_found"] is None
    assert body["candidates"][0]["name"] == "Ramesh Kumar" and body["candidates"][0]["confidence_label"] == voiceprint.UNCALIBRATED_LABEL
    assert "must not be read as a match" in body["note"] and "requires forensic voice expert confirmation" in body["label"]
    call = pa.log_action.call_args.kwargs
    assert call["action"] == "voiceprint_match" and call["extra"]["lawful_interception_ref"] == REF and call["extra"]["calibrated"] is False
    assert call["extra"]["top_candidate"] == "Ramesh Kumar" and call["extra"]["jurisdiction_filter"] == "Test"
    idx.calibration = calibration()
    r = post(client, "match", "investigator", lawful_interception_ref=REF)
    assert r.json()["calibrated"] is True and r.json()["match_found"] is True and r.json()["note"] is None
    st = client.get("/api/v1/biometric/voice/status", headers=_auth_headers("investigator")).json()
    assert st["calibrated"] is True and st["enrolled"] == 1


def test_match_is_scoped_and_refuses_poor_audio_and_bad_files(api):
    client, idx, _ = api
    idx.enroll("Elsewhere", "F9", recording(0), REF, jurisdiction="West Range")
    r = post(client, "match", "investigator", lawful_interception_ref=REF)                                        # investigator is scoped to "Test"
    assert r.status_code == 200 and r.json()["candidates"] == []
    assert post(client, "match", "admin", lawful_interception_ref=REF).json()["candidates"][0]["name"] == "Elsewhere"
    short = post(client, "match", "investigator", audio=recording(0, seconds=1.0), lawful_interception_ref=REF)
    assert short.status_code == 422 and short.json()["detail"]["quality_too_low"] is True and pa.log_action.call_args.kwargs["extra"]["quality_passed"] is False
    assert post(client, "match", "investigator", audio=b"not audio at all", lawful_interception_ref=REF).status_code == 415
    assert post(client, "match", "investigator", filename="x.txt", lawful_interception_ref=REF).status_code == 415


def test_unavailable_engine_is_501_with_an_explanation(api):
    client, idx, _ = api
    idx.engine = voiceprint.UnavailableEngine()
    idx._decode = lambda a, s: (_ for _ in ()).throw(AssertionError("decode is not reached"))
    idx._decode = fake_decode
    r = post(client, "match", "investigator", lawful_interception_ref=REF)
    assert r.status_code == 501 and "INSTALL_VOICEPRINT" in r.json()["detail"]
    assert client.get("/api/v1/biometric/voice/status", headers=_auth_headers("investigator")).json()["available"] is False


def test_production_refuses_enabled_but_uncalibrated_voice_endpoints(api):
    _, idx, va = api
    va.assert_ready_for_production(idx, dev_mode=True)                                          # development may run ranking-only
    with pytest.raises(RuntimeError, match="no valid calibration file"):
        va.assert_ready_for_production(idx, dev_mode=False)
    idx.calibration = calibration()
    va.assert_ready_for_production(idx, dev_mode=False)


def test_engine_loading_defaults_to_unavailable_and_survives_a_bad_spec():
    assert isinstance(voiceprint.load_engine(""), voiceprint.UnavailableEngine)
    assert isinstance(voiceprint.load_engine("no.such.module:Engine"), voiceprint.UnavailableEngine)
    assert voiceprint.SpeechBrainEngine().model_id == "speechbrain/spkrec-ecapa-voxceleb"
    assert isinstance(voiceprint.load_engine("voiceprint:SpeechBrainEngine"), voiceprint.SpeechBrainEngine)      # constructing does not load the model


# ---------------------------------------------------------------------------
# Evaluation script (pure functions)
# ---------------------------------------------------------------------------

@pytest.fixture
def ev(np_real):
    spec = importlib.util.spec_from_file_location("evaluate_voiceprint", ROOT / "scripts/evaluate_voiceprint.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_voiceprint"] = module
    spec.loader.exec_module(module)
    return module


def synthetic_dataset(np, speakers_per_language=20, spread=0.15, seed=0):
    """3 languages x 20 speakers, each recorded on both channels in two sessions; recordings cluster tightly around the speaker."""
    rng = np.random.default_rng(seed)
    vectors, meta = [], []
    for lang in ("hi", "en", "bn"):
        for s in range(speakers_per_language):
            centre = rng.normal(size=voiceprint.EMBEDDING_DIM)
            for channel in ("phone", "direct"):
                for session in ("s1", "s2"):
                    v = centre + rng.normal(0, spread * (3 if channel == "phone" else 1), voiceprint.EMBEDDING_DIM)
                    vectors.append(v / np.linalg.norm(v))
                    meta.append({"speaker_id": f"{lang}{s}", "language": lang, "channel": channel, "session": f"{channel}-{session}", "net_speech_seconds": 12.0, "gender": "f" if s % 2 else "m"})
    return np.vstack(vectors).astype(np.float32), meta


def test_eer_and_thresholds_on_separable_and_random_scores(ev, np_real):
    np = np_real
    rng = np.random.default_rng(0)
    e, t = ev.eer_of(rng.normal(0.9, 0.02, 500), rng.normal(0.1, 0.05, 5000))
    assert e < 0.001 and 0.3 < t < 0.9                                                       # cleanly separated
    e2, _ = ev.eer_of(rng.normal(0.5, 0.1, 2000), rng.normal(0.5, 0.1, 2000))
    assert 0.4 < e2 < 0.6                                                                    # indistinguishable: ~50%
    assert np.isnan(ev.eer_of(np.array([]), np.array([0.1]))[0])
    imp = rng.normal(0, 1, 20000)
    t01 = ev.threshold_at_far(imp, 0.001)
    assert abs((imp >= t01).mean() - 0.001) < 0.0005 and ev.frr_at(np.array([0.0, 5.0]), t01) == 0.5
    assert ev.threshold_at_far(imp[:500], 0.001) is None                                     # too few impostor trials to support 0.1%
    assert ev.threshold_at_far(imp, 0.0001) is None                                          # 0.01% needs ~100,000 impostor trials; 20,000 cannot support it
    assert ev.threshold_at_far(rng.normal(0, 1, 120_000), 0.0001) is not None


def test_analysis_breaks_results_down_and_flags_a_hidden_bad_subgroup(ev, np_real):
    np = np_real
    vectors, meta = synthetic_dataset(np)
    result = ev.analyse(vectors, meta, bootstrap=30)
    assert result["counts"]["speakers"] == 60 and result["counts"]["languages"] == ["bn", "en", "hi"] and result["counts"]["channels"] == ["direct", "phone"]
    assert all(v == 20 for v in result["counts"]["speakers_per_cell"].values()) and len(result["counts"]["speakers_per_cell"]) == 6
    assert result["overall"]["eer"] is not None and result["overall"]["genuine_trials"] > 0 and result["overall"]["impostor_trials"] > 10_000
    assert set(result["breakdown"]) == {"language", "channel_pairing", "duration_band", "gender"}
    assert set(result["breakdown"]["channel_pairing"]) == {"direct+direct", "direct+phone", "phone+phone"}
    assert result["breakdown"]["channel_pairing"]["phone+phone"]["eer"] >= result["breakdown"]["channel_pairing"]["direct+direct"]["eer"]     # noisier channel is worse
    assert result["bootstrap"]["replicates"] == 30
    # a subgroup that is far worse than the average must be called out, not averaged away
    fake = {"overall": {"eer": 0.01}, "breakdown": {"language": {"ta": {"eer": 0.12, "genuine_trials": 200}, "hi": {"eer": 0.01, "genuine_trials": 500},
                                                      "tiny": {"eer": 0.5, "genuine_trials": 4}}}}
    assert ev.worst_subgroups(fake) == ["language=ta: EER 12.0% vs overall 1.0%"]                 # the 4-trial group is too small to judge


def test_same_session_pairs_are_not_genuine_trials(ev, np_real):
    np = np_real
    v = np.eye(4, voiceprint.EMBEDDING_DIM, dtype=np.float32)
    meta = [{"speaker_id": "a", "language": "en", "channel": "direct", "session": "s1"}, {"speaker_id": "a", "language": "en", "channel": "direct", "session": "s1"},
            {"speaker_id": "a", "language": "en", "channel": "direct", "session": "s2"}, {"speaker_id": "b", "language": "en", "channel": "direct", "session": "s1"}]
    ps = ev.pair_scores(v, meta)
    assert int(ps["genuine"].sum()) == 2 and int(ps["impostor"].sum()) == 3                       # a1-a3 and a2-a3 only; a1-a2 (same session) excluded


def test_insufficient_data_never_becomes_a_calibration(ev, np_real):
    np = np_real
    vectors, meta = synthetic_dataset(np, speakers_per_language=3)                             # 9 speakers: a colleague-sized test set
    result = ev.analyse(vectors, meta)
    short = voiceprint.calibration_shortfalls(result["counts"])
    assert any("at least 50" in s for s in short) and any("speakers" in s and "cell" in s for s in short) and any("impostor" in s for s in short)
    vectors, meta = synthetic_dataset(np)
    counts = ev.analyse(vectors, meta)["counts"]
    assert voiceprint.calibration_shortfalls(counts) == []
    no_regional = [m for m in meta if m["language"] != "bn"]
    assert any("at least 3" in s for s in voiceprint.calibration_shortfalls(ev.analyse(vectors[[i for i, m in enumerate(meta) if m["language"] != "bn"]], no_regional)["counts"]))


def test_a_written_calibration_round_trips_into_the_service(ev, np_real, tmp_path):
    np = np_real
    vectors, meta = synthetic_dataset(np)
    result = ev.analyse(vectors, meta)
    doc = ev.build_calibration(result, "fake/ecapa", "abc123")
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(doc))
    cal, why = voiceprint.load_calibration(str(path), "fake/ecapa")
    assert cal is not None, why
    assert cal.thresholds["far_0_1pct"] == doc["thresholds"]["far_0_1pct"] and cal.thresholds["far_0_1pct"] is not None
    assert doc["manifest_sha256"] == "abc123" and doc["counts"]["speakers"] == 60
    assert voiceprint.load_calibration(str(path), "some/other-model")[0] is None                # a model change forces re-evaluation


def test_manifest_loading_rejects_unknown_channels(ev, tmp_path):
    good = tmp_path / "m.csv"
    good.write_text("path,speaker_id,language,channel\na.wav,s1,HI,Phone\nb.wav,s1,en,direct\n")
    rows, sha = ev.load_manifest(good)
    assert rows[0]["language"] == "hi" and rows[0]["channel"] == "phone" and len(sha) == 64
    bad = tmp_path / "bad.csv"
    bad.write_text("path,speaker_id,language,channel\na.wav,s1,hi,landline\n")
    with pytest.raises(SystemExit):
        ev.load_manifest(bad)
    missing = tmp_path / "missing.csv"
    missing.write_text("path,speaker\na.wav,s1\n")
    with pytest.raises(SystemExit):
        ev.load_manifest(missing)
