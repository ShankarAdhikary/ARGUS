"""Live contract tests for the seeded Compose stack.

Run only against a disposable synthetic-data environment:
    ARGUS_LIVE_TESTS=1 pytest -q tests/integration/test_live_api.py
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

if not os.getenv("ARGUS_LIVE_TESTS"):
    pytest.skip("Set ARGUS_LIVE_TESTS=1 to run against the live synthetic stack.", allow_module_level=True)

from main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, employee_id: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"employee_id": employee_id, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_auth_success_and_failure(client: TestClient) -> None:
    token = login(client, "admin@demo.com", "password123")
    assert token
    assert client.post(
        "/api/v1/auth/login",
        json={"employee_id": "admin@demo.com", "password": "wrong-password"},
    ).status_code == 401


def test_sensitive_case_requires_justification(client: TestClient) -> None:
    token = login(client, "admin@demo.com", "password123")
    headers = {"Authorization": f"Bearer {token}"}
    cases = client.get("/api/v1/cases", headers=headers)
    assert cases.status_code == 200
    case = next(item for item in cases.json() if item["is_sensitive"])
    denied = client.get(f"/api/v1/cases/{case['case_id']}", headers=headers)
    assert denied.status_code == 428
    allowed = client.get(
        f"/api/v1/cases/{case['case_id']}?justification=Subject%20is%20a%20Politically%20Exposed%20Person",
        headers=headers,
    )
    assert allowed.status_code == 200
    audit = client.get("/api/v1/audit", headers=headers)
    assert audit.status_code == 200
    assert any(
        entry["action"] == "SENSITIVE_VIEW"
        and entry["resource"] == f"case:{case['case_id']}"
        and entry["justification"] == "Subject is a Politically Exposed Person"
        for entry in audit.json()
    )


def test_ingestion_health_is_live_and_role_protected(client: TestClient) -> None:
    admin = login(client, "admin@demo.com", "password123")
    response = client.get("/api/v1/admin/ingestion-health", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200
    assert {"status", "lastRun", "processed"} <= response.json().keys()

    investigator = login(client, "INV001", "demo123")
    denied = client.get("/api/v1/admin/ingestion-health", headers={"Authorization": f"Bearer {investigator}"})
    assert denied.status_code == 403


def test_graph_alerts_and_export_are_live(client: TestClient) -> None:
    token = login(client, "admin@demo.com", "password123")
    headers = {"Authorization": f"Bearer {token}"}
    graph = client.get("/api/v1/network/accused", params={"accused_name": "Alpha Holdings"}, headers=headers)
    assert graph.status_code == 200
    payload = graph.json()
    if "phone_contacts" not in payload:
        pytest.skip("Alpha Holdings AML narrative not loaded; run scripts/seed_demo.py on a disposable stack.")
    assert len(payload["phone_contacts"]) >= 4

    alerts = client.get("/api/v1/alerts", headers=headers)
    assert alerts.status_code == 200
    assert any(alert["entity_value"] == "Alpha Holdings" for alert in alerts.json())

    case = client.get("/api/v1/cases", headers=headers).json()[0]
    export = client.post(
        "/api/v1/reports/export",
        headers=headers,
        json={
            "case_id": case["case_id"],
            "sections": ["Entity List", "Notes"],
            "justification": "Live export verification for authorized investigation",
        },
    )
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/pdf")
    assert "attachment" in export.headers["content-disposition"]
