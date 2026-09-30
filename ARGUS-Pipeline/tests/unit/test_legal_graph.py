"""Phase 2: LegalCharge / Victim graph layer and its endpoints."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import indic_text
import legal_graph
import platform_api as pa
from tests.unit.test_platform_critical_path import _auth_headers

pytestmark = pytest.mark.skipif(indic_text.sanscript is None, reason="indic-transliteration not installed")

AADHAAR = "1234 5678 9012"


def test_victim_hash_is_keyed_stable_and_never_raw():
    h = legal_graph.victim_hash(AADHAAR)
    assert h == legal_graph.victim_hash("123456789012") == legal_graph.victim_hash("१२३४ ५६७८ ९०१२")
    assert len(h) == 64 and "123456789012" not in h
    assert h != __import__("hashlib").sha256(b"123456789012").hexdigest()  # keyed, not a bare hash
    assert legal_graph.victim_hash("") is None


def test_charges_from_sections_and_text():
    record = {"fir_id": "F1", "date": "2026-01-02", "sections": ["IPC 354D", "BNS 64(2)"], "description": "also u/s 506 IPC"}
    got = {(c["charge_id"], c["offense_category"]) for c in legal_graph.charges_for_record(record)}
    assert got == {("F1:IPC:354D", "STALKING"), ("F1:BNS:64(2)", "SEXUAL_OFFENCE"), ("F1:IPC:506", "INTIMIDATION")}


def test_victim_ids_and_pii_stripping():
    record = {"fir_id": "F1", "victim_aadhaar": AADHAAR, "description": f"The victim's Aadhaar {AADHAAR} was recorded."}
    ids = legal_graph.victim_ids_for_record(record)
    assert ids == [legal_graph.victim_hash(AADHAAR)]          # field and text mention collapse to one victim
    legal_graph.strip_victim_pii(record)
    assert "victim_aadhaar" not in record and "5678" not in record["description"]


def test_write_legal_layer_runs_cypher_per_charge_and_victim():
    session = MagicMock()
    out = legal_graph.write_legal_layer(session, {"fir_id": "F1", "jurisdiction": "J", "sections": ["IPC 376"], "victim_token": AADHAAR}, "Ramesh")
    assert out == {"charges": 1, "victims": 1} and session.run.call_count == 2
    victim_call = session.run.call_args_list[1]
    assert victim_call.kwargs["categories"] == ["SEXUAL_OFFENCE"]
    assert AADHAAR.replace(" ", "") not in str(session.run.call_args_list)


def test_co_charge_matrix_symmetric():
    m = legal_graph.co_charge_matrix([{"a": "IPC 354", "b": "IPC 509", "count": 3}])
    assert m["labels"] == ["IPC 354", "IPC 509"] and m["matrix"] == [[0, 3], [3, 0]]


def _driver(monkeypatch, rows):
    session = MagicMock()
    session.run.return_value = rows
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(pa, "_neo4j", driver)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    return session


@pytest.fixture
def client():
    import main
    return TestClient(main.app)


def test_charges_endpoint_builds_subgraph_and_scopes(monkeypatch, client):
    session = _driver(monkeypatch, [{"charge_id": "F1:IPC:354D", "section": "354D", "act": "IPC", "category": "STALKING",
                                    "date_filed": "2026-01-02", "confidence": 0.95, "fir_id": "F1", "jurisdiction": "Test"}])
    r = client.get("/api/v1/network/charges", params={"person_name": "Ramesh"}, headers=_auth_headers("investigator"))
    body = r.json()
    assert r.status_code == 200 and body["total_charges"] == 1 and "verify before use" in body["label"]
    assert {n["type"] for n in body["nodes"]} == {"Suspect", "LegalCharge", "FIR"} and len(body["edges"]) == 2
    assert session.run.call_args.kwargs["scope"] == "Test"      # investigator is confined to their jurisdiction


def test_repeat_victims_requires_supervisor_and_returns_no_raw_ids(monkeypatch, client):
    _driver(monkeypatch, [{"vid": "a" * 64, "repeat_count": 3, "cats": ["STALKING"], "jurisdiction": "Test"}])
    assert client.get("/api/v1/analytics/repeat-victims", headers=_auth_headers("investigator")).status_code == 403
    assert client.get("/api/v1/analytics/repeat-victims", headers=_auth_headers("analyst")).status_code == 403
    r = client.get("/api/v1/analytics/repeat-victims", headers=_auth_headers("supervisor"))
    body = r.json()
    assert r.status_code == 200 and body["repeat_victim_count"] == 1
    assert body["victims"][0]["victim_ref"] == "a" * 10 and "a" * 64 not in r.text
    assert body["offense_categories"] == [{"category": "STALKING", "victims": 1}]


def test_charge_patterns_merges_orientations(monkeypatch, client):
    _driver(monkeypatch, [
        {"a_act": "IPC", "a_sec": "354", "b_act": "IPC", "b_sec": "509", "n": 2},
        {"a_act": "IPC", "a_sec": "509", "b_act": "IPC", "b_sec": "354", "n": 1},
    ])
    body = client.get("/api/v1/analytics/charge-patterns", headers=_auth_headers("investigator")).json()
    assert body["pairs"] == [{"a": "IPC 354", "b": "IPC 509", "count": 3}]
