"""scripts/evaluate_fingerprint.py, the shared statistics, and per-mode calibration in the service.

Uses the synthetic engine from test_fingerprint (a "print" is a JPEG-magic byte string carrying a list of minutiae): it exercises
the trial logic, tables, refusals and the calibration round trip without a real matcher.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import fingerprint
from tests.unit.real_libs import real_libs
from tests.unit.test_fingerprint import KEY, MemoryStore, SyntheticEngine, make_index, print_image, pts

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def np_real():
    with real_libs(scipy=False):
        import numpy as np

        yield np


@pytest.fixture
def ev(np_real):
    for name in ("evalstats", "evaluate_fingerprint"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("evaluate_fingerprint", ROOT / "scripts/evaluate_fingerprint.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_fingerprint"] = module
    spec.loader.exec_module(module)
    return module


def entry(ev, engine, path, finger, ptype, minutiae, origin=None, session=None, subject=""):
    tpl = engine.extract(print_image(minutiae))
    return ev.Entry(path, finger, ptype, origin or ("scan" if ptype == "rolled" else "crop"), session or path, subject, tpl)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def test_upper_bound_does_not_pretend_zero_errors_means_zero_rate(np_real):
    import evalstats

    assert evalstats.upper_bound(0, 180) == pytest.approx(3 / 180)                    # rule of three: under ~1.7%, not "0"
    assert evalstats.upper_bound(0, 0) is None
    ub = evalstats.upper_bound(10, 1000)
    assert 0.010 < ub < 0.020 and evalstats.upper_bound(1000, 1000) == pytest.approx(1.0, abs=0.01)
    assert evalstats.upper_bound(5, 100) > 0.05                                        # always above the observed rate
    assert evalstats.rate_at(np_real.array([1.0, 2.0, 3.0]), 2.0, above=True) == pytest.approx(2 / 3)
    assert evalstats.rate_at(np_real.array([1.0, 2.0, 3.0]), 2.0, above=False) == pytest.approx(1 / 3)
    assert evalstats.rate_at(np_real.array([]), 2.0, above=True) is None


# ---------------------------------------------------------------------------
# Trials
# ---------------------------------------------------------------------------

def test_trials_split_genuine_impostor_and_same_person_and_skip_same_impression(ev):
    e = SyntheticEngine()
    entries = [
        entry(ev, e, "a1", "A", "rolled", pts(1), session="s1", subject="P1"),
        entry(ev, e, "a2", "A", "rolled", pts(1), session="s2", subject="P1"),          # same finger, other impression
        entry(ev, e, "b1", "B", "rolled", pts(2), session="s3", subject="P1"),          # same person, other finger
        entry(ev, e, "c1", "C", "rolled", pts(3), session="s4", subject="P2"),
        entry(ev, e, "lat", "A", "latent", pts(1)[:14], origin="lift", session="s9", subject="P1"),
        entry(ev, e, "crop", "A", "latent", pts(1)[:14], origin="crop", session="s1", subject="P1"),   # a crop of a1: shares its session
    ]
    t = ev.score_trials(entries, e)
    rolled = t["rolled"]
    assert rolled["probes"] == 4 and len(rolled["genuine"]) == 2                          # a1->a2 and a2->a1 (never a1->a1)
    assert len(rolled["impostor_same_subject"]) == 4 and len(rolled["impostor"]) == 6      # A<->B and a*<->b same person; the rest different people
    assert all(s == 100.0 for s in rolled["genuine"])
    latent = t["latent"]                                                                  # a real lift vs the gallery
    assert latent["probes"] == 1 and len(latent["genuine"]) == 2 and len(latent["impostor"]) + len(latent["impostor_same_subject"]) == 2
    crop = t["latent_crop"]                                                               # the crop is never scored against its own parent a1
    assert crop["probes"] == 1 and len(crop["genuine"]) == 1                              # only a2 counts as genuine
    assert ev.mode_of(entries[4]) == "latent" and ev.mode_of(entries[5]) == "latent_crop" and ev.mode_of(entries[0]) == "rolled"


def test_threshold_table_covers_20_to_100_with_counts_and_upper_bounds(ev, np_real):
    np = np_real
    genuine = np.array([150.0, 90.0, 60.0, 30.0])
    impostor = np.array([5.0, 12.0, 25.0, 41.0, 8.0, 3.0, 55.0, 2.0, 1.0, 0.0])
    rows = ev.threshold_table(genuine, impostor)
    assert [r["threshold"] for r in rows] == list(range(20, 101, 5)) and len(rows) == 17
    at40 = next(r for r in rows if r["threshold"] == 40)
    assert at40["false_accepts"] == 2 and at40["far"] == pytest.approx(0.2) and at40["frr"] == pytest.approx(0.25) and at40["far_upper_95"] > at40["far"]
    at100 = rows[-1]
    assert at100["false_accepts"] == 0 and at100["far"] == 0 and at100["far_upper_95"] == pytest.approx(3 / 10) and at100["frr"] == pytest.approx(0.75)
    assert [r["far"] for r in rows] == sorted((r["far"] for r in rows), reverse=True)     # FAR never rises with the threshold
    assert [r["frr"] for r in rows] == sorted(r["frr"] for r in rows)                     # FRR never falls
    text = ev.format_table("rolled", {"probes": 4, "genuine_trials": 4, "impostor_trials": 10, "impostor_same_person_other_finger": 0, "eer": 0.2, "threshold_eer": 40,
                                      "genuine_scores": {}, "impostor_scores": {}, "table": rows})
    assert "could be as high as" in text and text.count("\n") > 17 and "100 |" in text


# ---------------------------------------------------------------------------
# Refusal and calibration
# ---------------------------------------------------------------------------

def big_entries(ev, e, fingers=120, with_lifts=0, lifts_origin="lift"):
    """A synthetic gallery of `fingers` fingers x 2 impressions (+ optional latent probes): big enough to pass the data minimums."""
    entries = []
    for f in range(fingers):
        base = pts(f + 1, 20)
        for k in (1, 2):
            entries.append(entry(ev, e, f"f{f}_{k}", f"F{f}", "rolled", base, session=f"F{f}-{k}", subject=f"P{f}"))
    for f in range(with_lifts):
        entries.append(entry(ev, e, f"lift{f}", f"F{f}", "latent", pts(f + 1, 20)[:10], origin=lifts_origin, session=f"L{f}", subject=f"P{f}"))
    return entries


def test_a_small_set_is_never_a_calibration(ev):
    e = SyntheticEngine()
    small = ev.evaluate(big_entries(ev, e, fingers=10), e)                                 # a colleague-sized set: 10 fingers, ~360 impostor trials
    ok, why = ev.calibratable_modes(small)
    assert ok == [] and any("10 fingers, need at least 100" in p for p in why["rolled"]) and any("impostor trials" in p for p in why["rolled"])
    assert "no probes" in why["latent"][0] and "origin=lift" in why["latent"][0]
    assert ev.build_calibration(small, "synthetic", "sha", "test", 0.001) is None


def test_enough_rolled_data_calibrates_rolled_only_and_crops_never_calibrate_latent(ev):
    e = SyntheticEngine()
    entries = big_entries(ev, e, fingers=120, with_lifts=60, lifts_origin="crop")          # 60 latent probes, but they are crops
    result = ev.evaluate(entries, e)
    ok, why = ev.calibratable_modes(result)
    assert ok == ["rolled"] and "latent_crop" in why and "never set a threshold" in why["latent_crop"][0]
    assert result["real_latent_probes"] == 0 and "latent" not in result["modes"]           # none of them counted as real latents
    cal = ev.build_calibration(result, "synthetic", "sha256hex", "NIST SD302, licence ref 123", 0.001)
    assert set(cal["modes"]) == {"rolled"} and cal["provenance"].startswith("NIST") and cal["modes"]["rolled"]["thresholds"]["operating"] is not None
    assert cal["modes"]["rolled"]["counts"]["fingers"] == 120 and cal["modes"]["rolled"]["counts"]["impostor_trials"] >= 10_000


def test_real_lifts_calibrate_the_latent_mode_but_need_enough_of_them(ev):
    e = SyntheticEngine()
    few = ev.evaluate(big_entries(ev, e, fingers=120, with_lifts=20), e)
    ok, why = ev.calibratable_modes(few)
    assert "latent" not in ok and any("real latent lifts" in p and "at least 50" in p for p in why["latent"])
    many = ev.evaluate(big_entries(ev, e, fingers=120, with_lifts=120), e)
    ok, _ = ev.calibratable_modes(many)
    assert ok == ["rolled", "latent"]


def test_operating_threshold_is_the_measured_one_and_labels_follow_it(ev, tmp_path):
    e = SyntheticEngine()
    result = ev.evaluate(big_entries(ev, e, fingers=120, with_lifts=120), e)
    cal_doc = ev.build_calibration(result, "synthetic", "sha", "unit test corpus", 0.001)
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(cal_doc))
    cal, why = fingerprint.load_calibration(str(path), "synthetic")
    assert cal is not None and why is None and set(cal.modes) == {"rolled", "latent"}
    idx = make_index(store=MemoryStore())
    idx.calibration = cal
    t_rolled = cal_doc["modes"]["rolled"]["thresholds"]["operating"]
    assert idx.threshold_for("rolled") == t_rolled and idx.calibrated("rolled") and idx.calibrated("latent")
    assert idx.label_for(t_rolled - 0.01, "rolled") == "Low / insufficient" and idx.label_for(t_rolled, "rolled") in ("Medium", "High")
    assert idx.label_for(t_rolled, "latent").endswith("/ latent match") or idx.label_for(t_rolled, "latent") == "Low / insufficient"
    plain = make_index()
    assert plain.threshold_for("latent") == fingerprint.MATCH_THRESHOLD and not plain.calibrated("latent")          # no calibration: unchanged defaults
    assert plain.label_for(45.0, "latent") == "Medium / latent match"


def test_service_refuses_calibrations_it_should_not_trust(ev, tmp_path):
    e = SyntheticEngine()
    result = ev.evaluate(big_entries(ev, e, fingers=120), e)
    doc = ev.build_calibration(result, "synthetic", "sha", "unit test corpus", 0.001)
    def load(d, engine="synthetic"):
        p = tmp_path / "c.json"
        p.write_text(json.dumps(d))
        return fingerprint.load_calibration(str(p), engine)
    assert load(doc)[0] is not None
    assert "different" in load(doc, "another-engine")[1] or "not 'another-engine'" in load(doc, "another-engine")[1]
    assert "where its data came from" in load({**doc, "provenance": "  "})[1] or "provenance" in load({**doc, "provenance": "  "})[1]
    thin = json.loads(json.dumps(doc)); thin["modes"]["rolled"]["counts"]["fingers"] = 12                           # a doctored count is checked, not trusted blindly
    assert load(thin)[0] is None and "minimum data" in load(thin)[1]
    assert fingerprint.load_calibration(str(tmp_path / "missing.json"), "synthetic")[0] is None
    (tmp_path / "junk.json").write_text("nope")
    assert "unreadable" in fingerprint.load_calibration(str(tmp_path / "junk.json"), "synthetic")[1]


def test_endpoint_uses_the_calibrated_threshold_per_print_type(monkeypatch):
    import main
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from tests.unit.test_platform_critical_path import _auth_headers

    idx = make_index()
    idx.calibration = fingerprint.Calibration("synthetic", "now", "unit test", {"rolled": {"operating": 90.0, "far_0_01pct": None},
                                                                                 "latent": {"operating": 30.0, "far_0_01pct": 95.0}})
    idx.enroll("Ramesh Kumar", "FIR-1", print_image(pts(1)), jurisdiction="Test")
    monkeypatch.setattr(main, "fingerprint_index", idx)
    monkeypatch.setattr(main, "log_action", MagicMock())
    client = TestClient(main.app)
    partial = print_image(pts(1)[:10] + pts(8, 10))                                        # scores 50
    post = lambda pt: client.post("/api/v1/biometric/fingerprint/match", data={"print_type": pt}, files={"file": ("q.jpg", partial, "image/jpeg")}, headers=_auth_headers("investigator")).json()
    rolled, latent = post("rolled"), post("latent")
    assert rolled["match_found"] is False and rolled["threshold_used"] == 90.0 and rolled["calibrated"] is True     # 50 < the measured rolled threshold of 90
    assert latent["match_found"] is True and latent["threshold_used"] == 30.0 and latent["candidates"][0]["confidence_label"] == "Medium / latent match"
    status = client.get("/api/v1/biometric/fingerprint/status", headers=_auth_headers("investigator")).json()
    assert status["thresholds"] == {"rolled": 90.0, "latent": 30.0} and status["calibrated"] == {"rolled": True, "latent": True}


# ---------------------------------------------------------------------------
# Manifest and files
# ---------------------------------------------------------------------------

def test_manifest_defaults_never_assume_a_latent_is_a_real_lift(ev, tmp_path):
    m = tmp_path / "m.csv"
    m.write_text("path,finger_id,print_type,origin,session,subject_id\na.png,F1,Rolled,,s1,P1\nb.png,F1,latent,,s2,P1\nc.png,F1,latent,lift,s3,P1\n")
    rows, sha = ev.load_manifest(m)
    assert [r["origin"] for r in rows] == ["scan", "crop", "lift"] and rows[0]["print_type"] == "rolled" and len(sha) == 64
    bad = tmp_path / "bad.csv"
    bad.write_text("path,finger_id,print_type\na.png,F1,inked\n")
    with pytest.raises(SystemExit):
        ev.load_manifest(bad)
    bad.write_text("path,finger_id,print_type,origin\na.png,F1,latent,scanner\n")
    with pytest.raises(SystemExit):
        ev.load_manifest(bad)
    bad.write_text("path,finger\na.png,F1\n")
    with pytest.raises(SystemExit):
        ev.load_manifest(bad)


def test_prepare_applies_the_services_own_floors_and_reports_refusals(ev, tmp_path):
    e = SyntheticEngine()
    (tmp_path / "ok.jpg").write_bytes(print_image(pts(1, 20)))
    (tmp_path / "sparse_rolled.jpg").write_bytes(print_image(pts(2, 9)))          # 9 minutiae: below the rolled floor (12)...
    (tmp_path / "sparse_latent.jpg").write_bytes(print_image(pts(3, 9)))          # ...but above the latent floor (8)
    (tmp_path / "blur.jpg").write_bytes(print_image(pts(4, 20), blur=True))
    rows = [{"path": p, "finger_id": f, "print_type": t, "origin": "scan", "session": p, "subject_id": ""}
            for p, f, t in (("ok.jpg", "A", "rolled"), ("sparse_rolled.jpg", "B", "rolled"), ("sparse_latent.jpg", "C", "latent"), ("blur.jpg", "D", "rolled"), ("gone.jpg", "E", "rolled"))]
    entries, refused = ev.prepare(rows, e, tmp_path)
    assert [x.path for x in entries] == ["ok.jpg", "sparse_latent.jpg"]
    reasons = {r["path"]: r["reason"] for r in refused}
    assert "floor is 12" in reasons["sparse_rolled.jpg"] and "blurry" in reasons["blur.jpg"] and "unreadable" in reasons["gone.jpg"]
