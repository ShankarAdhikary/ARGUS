"""Phase 4: geocoder, KDE hotspots, risk diffusion and their endpoints."""

import random
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import geo_api
import geo_risk
import platform_api as pa
from tests.unit.test_platform_critical_path import _auth_headers


from tests.unit.real_libs import real_libs as real_scipy


def test_geocode_station_district_and_text():
    assert geo_risk.geocode(station="Saket Police Station")["level"] == "station"
    pune = geo_risk.geocode(district="Pune")
    assert pune["level"] == "district" and 18 < pune["lat"] < 19 and 73 < pune["lon"] < 75
    assert geo_risk.geocode(text="Incident reported at Hauz Khas Police Station last night")["place"] == "Hauz Khas Police Station"
    assert geo_risk.geocode(station="Nowhere PS", text="nothing here") is None


def test_geocode_record_explicit_point_wins_and_bad_values_ignored():
    assert geo_risk.geocode_record({"lat": 28.6, "lon": 77.2, "station": "Saket Police Station"})["level"] == "point"
    assert geo_risk.geocode_record({"lat": 999, "lon": 77.2, "station": "Saket Police Station"})["level"] == "station"


def test_hotspots_levels_are_nested():
    random.seed(3)
    pts = [(28.6 + random.gauss(0, 0.02), 77.2 + random.gauss(0, 0.02)) for _ in range(40)]
    with real_scipy():
        fc = geo_risk.hotspots(pts, category="WOMEN_SAFETY")
    levels = {f["properties"]["level"]: f["properties"]["cells"] for f in fc["features"]}
    assert set(levels) == {"high", "medium", "low"} and levels["high"] < levels["medium"] < levels["low"]
    assert fc["features"][0]["geometry"]["type"] == "MultiPolygon" and "verify before use" in fc["properties"]["label"]


def test_hotspots_need_enough_points():
    with real_scipy():
        assert geo_risk.hotspots([(28.6, 77.2)] * 5)["features"] == []
        assert "collinear" in geo_risk.hotspots([(28.6, 77.2), (28.7, 77.3), (28.8, 77.4)])["properties"]["reason"]


def test_diffusion_formula_and_ranking():
    out = geo_risk.diffuse_risk("A", {"A": 80, "B": 10, "C": 0}, {"B": 2, "C": 1, "D": 0}, 4, decay=0.5, adjacent={"B"})
    b = next(r for r in out if r["jurisdiction"] == "B")
    assert b["score"] == 20.0                                    # 80 * 2/4 * 0.5
    c = next(r for r in out if r["jurisdiction"] == "C")
    assert c["score"] == 5.0 and not c["geographic_neighbour"]  # 80 * 1/4 * (0.5 * 0.5)
    assert [r["jurisdiction"] for r in out] == ["B", "C"]       # D shares nothing
    assert geo_risk.diffuse_risk("A", {"A": 80}, {"B": 1}, 0) == []
    assert len(geo_risk.diffuse_risk("A", {"A": 1}, {str(i): 1 for i in range(9)}, 9, top=5)) == 5


def test_jurisdiction_risk_blend():
    g = {"suspects": {"J1": {"a"}, "J2": {"b"}}, "ws_firs": {"J1": {"f1", "f2"}, "J2": {"f3"}}, "wsrs": {"J1": [80.0], "J2": []}}
    r = geo_api.jurisdiction_risk(g)
    assert r["J1"] == 90.0 and r["J2"] == 25.0


@pytest.fixture
def client():
    import main
    return TestClient(main.app)


def test_hotspots_endpoint_scopes_and_labels(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(geo_api, "_fir_points", lambda cat, scope: captured.update(cat=cat, scope=scope) or [(1.0, 1.0)])
    monkeypatch.setattr(geo_risk, "hotspots", lambda pts, g, pad, cat: {"type": "FeatureCollection", "features": [], "properties": {"category": cat}})
    monkeypatch.setattr(pa, "log_action", MagicMock())
    r = client.get("/api/v1/analytics/hotspots", headers=_auth_headers("investigator"))
    assert r.status_code == 200 and captured == {"cat": "WOMEN_SAFETY", "scope": "Test"}
    assert "explanation" in r.json()["properties"]
    assert client.get("/api/v1/analytics/hotspots", params={"category": "x; DROP"}, headers=_auth_headers("investigator")).status_code == 422


def test_forecast_requires_cross_jurisdiction_role(monkeypatch, client):
    monkeypatch.setattr(pa, "log_action", MagicMock())
    monkeypatch.setattr(geo_api, "_forecast", lambda j, d, t: {"found": True, "source_risk": 50.0, "suspects_in_source": 3, "forecast": [{"jurisdiction": "B", "score": 10}]})
    assert client.get("/api/v1/analytics/risk-forecast", params={"jurisdiction": "A"}, headers=_auth_headers("investigator")).status_code == 403
    r = client.get("/api/v1/analytics/risk-forecast", params={"jurisdiction": "A"}, headers=_auth_headers("analyst"))
    assert r.status_code == 200 and r.json()["forecast"][0]["jurisdiction"] == "B"
    monkeypatch.setattr(geo_api, "_forecast", lambda j, d, t: {"found": False})
    assert client.get("/api/v1/analytics/risk-forecast", params={"jurisdiction": "Z"}, headers=_auth_headers("admin")).status_code == 404
