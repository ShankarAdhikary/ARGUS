"""Phase 3: Women Safety Risk Score."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import platform_api as pa
import wsrs
from tests.unit.test_platform_critical_path import _auth_headers

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def fir(fid, date, cats=(), ws_text=False, **kw):
    return {"fir_id": fid, "date": date, "categories": list(cats), "ws_text": ws_text, "jurisdiction": "J", "station": "PS1", **kw}


def test_weights_sum_to_one_and_tiers():
    assert abs(sum(wsrs.WEIGHTS.values()) - 1) < 1e-9
    assert [wsrs.tier_for(s) for s in (39.9, 40, 69.9, 70)] == ["LOW", "MEDIUM", "MEDIUM", "HIGH"]


def test_non_ws_fir_is_ignored_and_text_only_is_lowest_severity():
    got = wsrs.ws_firs([fir("A", "2026-01-01", ["THEFT"]), fir("B", "2026-01-02", ws_text=True), fir("C", "2026-01-03", ["ACID_ATTACK", "HARASSMENT"])])
    assert [(f["fir_id"], f["severity"], f["source"]) for f in got] == [("B", 1, "text"), ("C", 5, "charge")]


def test_recency_decays_and_labels():
    fresh, label = wsrs._recency([fir("A", "2026-09-20")], NOW)
    assert fresh > 85 and label == "Active in last 30 days"
    old, label = wsrs._recency([fir("A", "2024-01-01")], NOW)
    assert old < 1 and label == "No WS FIR in over a year"


def test_escalation_counts_step_ups_only():
    seq = lambda *sev: [{"severity": s} for s in sev]
    assert wsrs._escalation(seq(2)) [0] == 0
    assert wsrs._escalation(seq(3, 3, 2)) == (0.0, "No escalation across FIRs")
    score, label = wsrs._escalation(seq(2, 5))
    assert score == 75 and label == "Charges escalated once"
    assert wsrs._escalation(seq(1, 2, 4))[1] == "Charges escalated 2 times"


def test_geographic_radius_and_fallback():
    tight = [{"lat": 28.6139, "lon": 77.2090, "geo_level": "point"}, {"lat": 28.6300, "lon": 77.2090, "geo_level": "point"}]   # ~1.8 km apart
    score, label = wsrs._geographic(tight)
    assert 85 < score < 95 and "1km" in label
    wide = [{"lat": 28.0, "lon": 77.0, "geo_level": "point"}, {"lat": 29.0, "lon": 77.0, "geo_level": "point"}]
    assert wsrs._geographic(wide)[0] == 0
    fallback, label = wsrs._geographic([{"station": "PS1"}, {"station": "PS1"}])
    assert fallback == 70 and "no coordinates" in label
    assert wsrs._geographic([{"station": "PS1"}])[0] == 0
    centroids = [{"lat": 28.5, "lon": 77.2, "geo_level": "station", "station": "PS1"}] * 2   # same centroid != tight radius
    assert wsrs._geographic(centroids)[1].endswith("(no coordinates)")


def test_score_breakdown_shape_and_total():
    firs = wsrs.ws_firs([fir("A", "2026-09-01", ["STALKING"]), fir("B", "2026-09-20", ["SEXUAL_OFFENCE"])])
    r = wsrs.score_suspect(firs, max_repeat=2, co_ws=2, network_score=45, now=NOW)
    assert set(r["factors"]) == {"recency", "repeat", "escalation", "network", "geographic"}
    assert all(set(v) == {"score", "label"} for v in r["factors"].values())
    expected = sum(wsrs.WEIGHTS[k] * (r["factors"][k]["score"]) for k in wsrs.WEIGHTS)
    assert abs(r["total"] - expected) < 0.6           # factor scores are rounded for display
    assert r["tier"] == wsrs.tier_for(r["total"]) and r["factors"]["network"]["label"] == "2 co-accused with WS history"
    assert "verify before use" in r["label"] and 0 < r["confidence"] <= 1
    assert wsrs.score_suspect([], max_repeat=1, co_ws=0, network_score=0) is None


def test_repeat_score_has_a_floor_denominator():
    one = wsrs.score_suspect(wsrs.ws_firs([fir("A", "2026-09-20", ["STALKING"])]), max_repeat=1, co_ws=0, network_score=0, now=NOW)
    assert one["factors"]["repeat"]["score"] == 33


def test_compute_all_network_and_ranking():
    data = {
        "Hub": [fir("F1", "2026-09-01", ["STALKING"]), fir("F2", "2026-09-10", ["SEXUAL_OFFENCE"]), fir("F3", "2026-09-25", ["SEXUAL_OFFENCE"])],
        "Mate1": [fir("F1", "2026-09-01", ["STALKING"])],
        "Mate2": [fir("F2", "2026-09-10", ["SEXUAL_OFFENCE"])],
        "Loner": [fir("F9", "2026-09-05", ["HARASSMENT"])],
        "Thief": [fir("F7", "2026-09-05", ["THEFT"])],
    }
    out = wsrs.compute_all(data, NOW)
    assert "Thief" not in out                                   # no WS FIRs -> no score, no label
    assert out["Hub"]["factors"]["network"]["score"] == 100     # most central WS co-accused
    assert out["Loner"]["factors"]["network"]["score"] == 0
    assert out["Hub"]["total"] > out["Mate1"]["total"] > out["Loner"]["total"] * 0 and out["Hub"]["total"] > out["Loner"]["total"]


def test_recompute_writes_affected_and_coaccused_and_clears_others():
    data_rows = [
        {"suspect": "Hub", "fir_id": "F1", "date": "2026-09-01", "jurisdiction": "J", "station": "P", "lat": None, "lon": None, "geo_level": None, "ws_text": False, "categories": ["STALKING"]},
        {"suspect": "Mate", "fir_id": "F1", "date": "2026-09-01", "jurisdiction": "J", "station": "P", "lat": None, "lon": None, "geo_level": None, "ws_text": False, "categories": ["STALKING"]},
        {"suspect": "Far", "fir_id": "F5", "date": "2026-09-01", "jurisdiction": "J", "station": "P", "lat": None, "lon": None, "geo_level": None, "ws_text": False, "categories": ["THEFT"]},
    ]
    session = MagicMock()
    session.run.side_effect = lambda q, **kw: data_rows if "RETURN s.id AS suspect" in q else None
    out = wsrs.recompute(session, ["Hub"], NOW)
    writes = [c.kwargs["id"] for c in session.run.call_args_list if "wsrs_score = $total" in c.args[0]]
    assert sorted(writes) == ["Hub", "Mate"] and set(out) == {"Hub", "Mate"}   # Far is untouched


@pytest.fixture
def client():
    import main
    return TestClient(main.app)


def _driver(monkeypatch, rows=None, single=None):
    session = MagicMock()
    session.run.return_value = MagicMock(single=MagicMock(return_value=single), __iter__=lambda s: iter(rows or []))
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(pa, "_neo4j", driver)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    return session


def test_person_endpoint_returns_breakdown_or_none(monkeypatch, client):
    breakdown = {"total": 73.2, "tier": "HIGH", "factors": {}}
    _driver(monkeypatch, single={"breakdown": __import__("json").dumps(breakdown), "updated": "t", "in_scope": 2})
    body = client.get("/api/v1/analytics/wsrs", params={"person_name": "X"}, headers=_auth_headers("investigator")).json()
    assert body["wsrs"]["total"] == 73.2
    _driver(monkeypatch, single={"breakdown": None, "updated": None, "in_scope": 0})
    assert client.get("/api/v1/analytics/wsrs", params={"person_name": "X"}, headers=_auth_headers("investigator")).json()["wsrs"] is None


def test_leaderboard_is_supervisor_only_and_scoped(monkeypatch, client):
    session = _driver(monkeypatch, rows=[{"suspect": "A", "score": 80, "tier": "HIGH", "breakdown": '{"total": 80}'}])
    assert client.get("/api/v1/analytics/wsrs-leaderboard", headers=_auth_headers("investigator")).status_code == 403
    r = client.get("/api/v1/analytics/wsrs-leaderboard", params={"jurisdiction": "Delhi"}, headers=_auth_headers("supervisor"))
    assert r.status_code == 200 and r.json()["suspects"][0]["suspect"] == "A"
    assert session.run.call_args.kwargs["scope"] == "Delhi"
    assert client.get("/api/v1/analytics/wsrs-leaderboard", params={"tier": "bogus"}, headers=_auth_headers("supervisor")).status_code == 422
    assert client.post("/api/v1/analytics/wsrs/recompute", headers=_auth_headers("supervisor")).status_code == 403
