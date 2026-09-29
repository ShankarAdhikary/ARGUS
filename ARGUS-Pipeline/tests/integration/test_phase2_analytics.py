"""Phase 2 live analytics and security checks against the 100+ demo dataset."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

if not os.getenv("ARGUS_LIVE_TESTS"):
    pytest.skip("Set ARGUS_LIVE_TESTS=1 to run against the live synthetic stack.", allow_module_level=True)

from main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"employee_id": "admin@demo.com", "password": "password123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_real_graph_analytics_are_non_degenerate(client: TestClient, headers: dict[str, str]) -> None:
    centrality = client.get("/api/v1/analytics/centrality", headers=headers)
    communities = client.get("/api/v1/analytics/communities", headers=headers)
    assert centrality.status_code == 200
    assert communities.status_code == 200
    rows = centrality.json()
    clusters = communities.json()
    assert len(rows) >= 10
    assert len({row["pagerank"] for row in rows}) >= 5
    assert len(clusters) >= 3
    assert max(cluster["size"] for cluster in clusters) < sum(cluster["size"] for cluster in clusters)


def test_surveillance_auth_and_cross_zone_alert(client: TestClient, headers: dict[str, str]) -> None:
    denied = client.post(
        "/api/v1/surveillance/event",
        json={
            "suspect_name": "Vikram Singh",
            "camera_id": "PHASE2-AUTH",
            "zone": "Central Bus Terminal",
            "timestamp": "2026-09-09T10:30:00",
            "match_confidence": 0.87,
        },
    )
    assert denied.status_code == 401

    first = client.post(
        "/api/v1/surveillance/event",
        headers=headers,
        json={
            "suspect_name": "Vikram Singh",
            "camera_id": "PHASE2-TEST-A",
            "zone": "Central Bus Terminal",
            "timestamp": "2026-09-10T10:30:00",
            "match_confidence": 0.87,
        },
    )
    second = client.post(
        "/api/v1/surveillance/event",
        headers=headers,
        json={
            "suspect_name": "Vikram Singh",
            "camera_id": "PHASE2-TEST-B",
            "zone": "East Market",
            "timestamp": "2026-09-10T10:52:00",
            "match_confidence": 0.84,
        },
    )
    assert first.status_code == second.status_code == 200
    assert any(alert["type"] == "CROSS_ZONE_MOVEMENT" for alert in second.json()["alerts"])


def test_resolution_never_auto_merges_and_flags_exact_phone_paths(
    client: TestClient, headers: dict[str, str]
) -> None:
    auto = client.get("/api/v1/resolve/check", params={"name": "Vikram Sing"}, headers=headers)
    review = client.get("/api/v1/resolve/check", params={"name": "Vikram Sng"}, headers=headers)
    phone = client.get("/api/v1/resolve/check", params={"name": "phone:9999988888"}, headers=headers)
    assert auto.status_code == review.status_code == phone.status_code == 200
    # FR-03: identities are never merged automatically; strong matches are only *suggested*.
    assert all(row["resolution"] == "review" for row in auto.json() + review.json() + phone.json())
    assert any(row["suggested"] == "merge" and row["similarity"] >= 0.92 for row in auto.json())
    assert any(row["suggested"] == "possible_match" for row in review.json())
    assert phone.json()[0]["match_type"] == "exact_phone"
    assert phone.json()[0]["suggested"] == "merge"


def test_sensitive_graph_is_blocked_and_audited(client: TestClient, headers: dict[str, str]) -> None:
    cases = client.get("/api/v1/cases", headers=headers)
    assert cases.status_code == 200
    case = next(item for item in cases.json() if item["is_sensitive"])

    denied = client.get(
        "/api/v1/network/accused",
        params={"accused_name": "Vikram Singh", "case_id": case["case_id"]},
        headers=headers,
    )
    assert denied.status_code == 428

    justification = "Phase 2 sensitive graph review"
    allowed = client.get(
        "/api/v1/network/accused",
        params={
            "accused_name": "Vikram Singh",
            "case_id": case["case_id"],
            "justification": justification,
        },
        headers=headers,
    )
    assert allowed.status_code == 200
    audit = client.get(
        "/api/v1/audit",
        params={"action": "SENSITIVE_GRAPH_VIEW", "start": "2026-01-01T00:00:00Z"},
        headers=headers,
    )
    assert audit.status_code == 200
    assert any(entry["justification"] == justification for entry in audit.json())


def test_regex_fallback_is_explicit_and_confidence_bearing(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/ingest/text",
        headers=headers,
        json={
            "source_id": f"phase2-regex-test-{uuid.uuid4().hex}",
            "source_type": "fir",
            "text": "FIR-2026-998 states that Suspect Vikram Singh used mobile 9999988888 near Central Bus Terminal.",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    if payload["extraction_method"] != "regex_fallback":
        pytest.skip("An LLM provider is configured; the regex-fallback path is not exercised in this environment.")
    person = next(entity for entity in payload["entities"] if entity["type"] == "person")
    location = next(entity for entity in payload["entities"] if entity["type"] == "location")
    assert person["confidence"] < 0.8
    assert location["confidence"] < 0.8
