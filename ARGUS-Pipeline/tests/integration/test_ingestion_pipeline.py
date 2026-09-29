"""Live ingestion tests for the real Compose MinIO/Redis/worker pipeline.

Run with:
    ARGUS_LIVE_TESTS=1 pytest -q tests/integration/test_ingestion_pipeline.py
"""

from __future__ import annotations

import json
import os
import time
import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from minio import Minio

if not os.getenv("ARGUS_LIVE_TESTS"):
    pytest.skip("Set ARGUS_LIVE_TESTS=1 to run against the live synthetic stack.", allow_module_level=True)

from config import BUCKET_NAME, MINIO_ACCESS_KEY, MINIO_ENDPOINT, MINIO_SECRET_KEY, MINIO_SECURE
from main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def wait_for_job(client: TestClient, job_id: str, expected: set[str]) -> dict:
    deadline = time.time() + 30
    while time.time() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in expected:
            return payload
        time.sleep(1)
    pytest.fail(f"Job {job_id} did not reach {expected}")


def test_upload_processes_and_duplicate_uses_checksum(client: TestClient) -> None:
    suffix = uuid.uuid4().hex
    payload = [{"fir_id": f"phase1-{suffix}", "accused": f"Phase One {suffix}", "mobile": "9111111111"}]
    content = json.dumps(payload).encode()

    first = client.post(
        "/api/v1/ingest",
        files={"file": ("phase1_fir.json", content, "application/json")},
    )
    assert first.status_code == 200, first.text
    first_job = first.json()["job_id"]
    assert wait_for_job(client, first_job, {"processed"})["sha256"]

    duplicate = client.post(
        "/api/v1/ingest",
        files={"file": ("renamed_cdr.json", content, "application/json")},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["status"] == "duplicate"
    assert duplicate.json()["job_id"] == first_job

    auth = client.post(
        "/api/v1/auth/login",
        json={"employee_id": "admin@demo.com", "password": "password123"},
    )
    assert auth.status_code == 200, auth.text
    search = client.get(
        "/api/v1/search/firs",
        params={"q": f"Phase One {suffix}"},
        headers={"Authorization": f"Bearer {auth.json()['access_token']}"},
    )
    assert search.status_code == 200, search.text
    assert any(row["fir_id"] == f"phase1-{suffix}" for row in search.json()["results"])


def test_malformed_file_is_quarantined_with_reason(client: TestClient) -> None:
    suffix = uuid.uuid4().hex
    response = client.post(
        "/api/v1/ingest",
        files={"file": (f"malformed_fir_{suffix}.json", b"{not-json", "application/json")},
    )
    assert response.status_code == 200, response.text
    job = wait_for_job(client, response.json()["job_id"], {"quarantined"})
    assert job["failure_reason"].startswith("Malformed JSON dataset:")


def test_unsupported_format_can_be_repaired_and_retried(client: TestClient) -> None:
    suffix = uuid.uuid4().hex
    content = json.dumps([{"marker": suffix}]).encode()
    response = client.post(
        "/api/v1/ingest",
        files={"file": (f"unsupported_{suffix}.json", content, "application/json")},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    job = wait_for_job(client, job_id, {"quarantined"})
    assert "Unsupported dataset format" in job["failure_reason"]

    minio = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )
    repaired = json.dumps(
        [{"fir_id": f"repaired-{suffix}", "accused": f"Repaired {suffix}", "mobile": "9222222222"}]
    ).encode()
    minio.put_object(BUCKET_NAME, job["storage_path"], BytesIO(repaired), len(repaired), content_type="application/json")

    admin = client.post(
        "/api/v1/auth/login",
        json={"employee_id": "admin@demo.com", "password": "password123"},
    )
    assert admin.status_code == 200, admin.text
    retry = client.post(
        f"/api/v1/jobs/{job_id}/retry?dataset_type=fir",
        headers={"Authorization": f"Bearer {admin.json()['access_token']}"},
    )
    assert retry.status_code == 200, retry.text
    assert wait_for_job(client, job_id, {"processed"})["status"] == "processed"
