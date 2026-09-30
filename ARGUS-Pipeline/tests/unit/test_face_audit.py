"""Face biometrics: every action is audit-logged and every search is jurisdiction-scoped.

The FaceIndex tests use the real module with real FAISS (conftest stubs both, so real_libs swaps them in); the endpoint tests
use a small fake index and the suite's stubs for DeepFace / OpenCV.
"""

import importlib.util
import zipfile
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import platform_api as pa
from tests.unit.real_libs import real_libs
from tests.unit.test_platform_critical_path import _auth_headers

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The real FaceIndex
# ---------------------------------------------------------------------------

@pytest.fixture
def real_index(tmp_path):
    with real_libs(scipy=False, faiss=True):
        import numpy as np

        spec = importlib.util.spec_from_file_location("real_face_index", ROOT / "face_index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        unit = lambda i: np.eye(8, dtype=np.float32)[i]                 # orthogonal unit vectors: similarity 1.0 only with themselves
        yield module.FaceIndex(8, str(tmp_path / "faces")), unit, module


def test_scoped_search_only_sees_the_callers_jurisdiction(real_index):
    idx, unit, _ = real_index
    idx.add(unit(0), {"hash_id": "a", "name": "Central Person", "fir_id": "F1", "jurisdiction": "Central District"})
    idx.add(unit(1), {"hash_id": "b", "name": "West Person", "fir_id": "F2", "jurisdiction": "West Range"})
    idx.add(unit(2), {"hash_id": "c", "name": "Legacy Person", "fir_id": "F3"})                 # enrolled before jurisdictions existed
    assert idx.search(unit(1))[1]["name"] == "West Person"                                     # unscoped: best face overall
    assert idx.search(unit(1), scope="Central District") is None or idx.search(unit(1), scope="Central District")[0] < 0.5
    hit = idx.search(unit(0), scope="Central District")
    assert hit and hit[1]["name"] == "Central Person" and hit[0] > 0.99
    assert idx.search(unit(2), scope="Central District") is None or idx.search(unit(2), scope="Central District")[0] < 0.5   # legacy = Unassigned
    assert idx.search(unit(2))[1]["name"] == "Legacy Person"                                   # ...but unscoped roles still see it
    assert idx.search(unit(2), scope="Unassigned")[1]["name"] == "Legacy Person"


def test_scoped_search_looks_past_closer_out_of_scope_faces(real_index):
    idx, unit, _ = real_index
    import numpy as np

    for i in range(4):                                                      # four out-of-scope faces all nearer the probe than the in-scope one
        idx.add(unit(0) * 0.99 + unit(i + 1) * 0.01 * (i + 1), {"hash_id": f"w{i}", "name": f"W{i}", "fir_id": "F", "jurisdiction": "West Range"})
    idx.add(unit(0) * 0.6 + unit(6) * 0.8, {"hash_id": "c", "name": "Central Person", "fir_id": "F", "jurisdiction": "Central District"})
    probe = unit(0)
    assert idx.search(probe)[1]["name"].startswith("W")                     # top-1 overall is out of scope
    hit = idx.search(probe, scope="Central District")
    assert hit and hit[1]["name"] == "Central Person"                       # a naive "top-1 then filter" would have returned nothing


def test_backfill_dry_run_apply_and_remove_where(real_index):
    idx, unit, _ = real_index
    idx.add(unit(0), {"hash_id": "a", "name": "A", "fir_id": "F-CENTRAL"})
    idx.add(unit(1), {"hash_id": "b", "name": "B", "fir_id": "N/A"})
    idx.add(unit(2), {"hash_id": "c", "name": "C", "fir_id": "F-WEST", "jurisdiction": "West Range"})
    resolve = lambda meta: {"F-CENTRAL": "Central District"}.get(meta["fir_id"])
    dry = idx.backfill_jurisdiction(resolve, apply=False)
    assert dry == {"already": 1, "resolved": 1, "unassigned": 1, "by_jurisdiction": {"Central District": 1, "Unassigned": 1}}
    assert "jurisdiction" not in idx._registry[0]                            # dry run wrote nothing
    idx.backfill_jurisdiction(resolve, apply=True)
    assert [m["jurisdiction"] for m in idx._registry] == ["Central District", "Unassigned", "West Range"]
    assert idx.backfill_jurisdiction(resolve)["resolved"] == 0               # idempotent
    assert idx.search(unit(0), scope="Central District")[1]["name"] == "A"
    assert idx.remove_where(lambda m: m["name"] == "B") == 1 and idx.ntotal == 2


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class FakeFaces:
    """Stands in for FaceIndex: a registry list, scope-aware search, add() recording what was stamped on each face."""
    def __init__(self):
        self.registry = []
        self.ntotal = 0

    def add(self, embedding, meta):
        self.registry.append(meta)
        self.ntotal += 1

    def search(self, embedding, scope=None):
        for meta in self.registry:
            if scope is None or meta.get("jurisdiction", "Unassigned") == scope:
                return 0.91, meta
        return None


@pytest.fixture
def api(monkeypatch, tmp_path):
    import main

    monkeypatch.chdir(tmp_path)                                  # unified-enroll / zip write scratch files relative to the cwd
    (tmp_path / "uploads").mkdir()
    faces = FakeFaces()
    monkeypatch.setattr(main, "face_index", faces)
    monkeypatch.setattr(main, "_represent", lambda img: [{"embedding": [0.1] * 512}])
    # The suite stubs numpy/cv2/faiss as near-empty modules; these endpoints only pass images and vectors through them.
    for name in ("np", "cv2", "faiss"):
        monkeypatch.setattr(main, name, MagicMock())
    monkeypatch.setattr(main, "log_action", MagicMock())
    monkeypatch.setattr(main, "_authorize_case", MagicMock())
    return TestClient(main.app), faces, main


def hunt(client, role, **form):
    return client.post("/api/v1/biometric/hunt", data=form, files={"file": ("p.jpg", b"\xff\xd8\xffFACEBYTES", "image/jpeg")}, headers=_auth_headers(role))


def enroll(client, role, name="Ramesh Kumar"):
    return client.post("/api/v1/biometric/enroll", data={"name": name}, files={"file": ("p.jpg", b"\xff\xd8\xffFACEBYTES", "image/jpeg")}, headers=_auth_headers(role))


def test_enroll_stamps_jurisdiction_and_audits(api):
    client, faces, main = api
    assert enroll(client, "investigator").json()["status"] == "success"      # test tokens carry jurisdiction "Test"
    assert enroll(client, "admin", name="Admin Enrolled").json()["status"] == "success"
    assert [m["jurisdiction"] for m in faces.registry] == ["Test", "Unassigned"]            # scoped: own; unscoped roles: Unassigned
    entries = [c.kwargs for c in main.log_action.call_args_list]
    assert [e["action"] for e in entries] == ["face_enroll", "face_enroll"]
    assert entries[0]["extra"]["jurisdiction"] == "Test" and len(entries[0]["extra"]["image_sha256"]) == 64 and entries[0]["extra"]["suspect_hash"]
    assert "Ramesh" not in json.dumps(entries)                               # the audit row carries hashes and ids, not the person's name


def test_hunt_is_scoped_and_every_search_is_audited(api):
    client, faces, main = api
    faces.add(None, {"hash_id": "w", "name": "West Person", "fir_id": "F2", "jurisdiction": "West Range"})
    faces.add(None, {"hash_id": "t", "name": "Test Person", "fir_id": "F1", "jurisdiction": "Test"})
    r = hunt(client, "investigator").json()                                  # scoped to "Test": the West Range face must not surface
    assert r["match_found"] is True and r["suspect_data"]["name"] == "Test Person"
    r = hunt(client, "admin").json()                                         # unscoped: nearest overall
    assert r["match_found"] is True and r["suspect_data"]["name"] == "West Person"
    faces.registry.clear(); faces.registry.append({"hash_id": "w", "name": "West Person", "fir_id": "F2", "jurisdiction": "West Range"})
    assert hunt(client, "investigator").json() == {"status": "success", "match_found": False}    # only an out-of-scope face exists: a clean miss
    calls = [c.kwargs for c in main.log_action.call_args_list]
    assert [c["action"] for c in calls] == ["face_hunt"] * 3
    assert calls[0]["extra"] | {"match_found": True} == calls[0]["extra"] and calls[0]["extra"]["jurisdiction_filter"] == "Test"
    assert calls[0]["extra"]["top_name"] == "Test Person" and len(calls[0]["extra"]["probe_sha256"]) == 64
    assert calls[1]["extra"]["jurisdiction_filter"] is None and calls[2]["extra"]["match_found"] is False and calls[2]["extra"]["top_name"] is None


def test_failed_hunt_is_still_audited_and_case_access_is_enforced(api, monkeypatch):
    client, faces, main = api
    monkeypatch.setattr(main, "_represent", MagicMock(side_effect=RuntimeError("no face detected")))
    r = hunt(client, "investigator").json()
    assert r["status"] == "error"
    failed = main.log_action.call_args.kwargs
    assert failed["action"] == "face_hunt" and failed["extra"]["error"] is True and failed["extra"]["match_found"] is False
    monkeypatch.setattr(main, "_represent", lambda img: [{"embedding": [0.1] * 512}])
    hunt(client, "investigator", case_id="C-1", justification="witness lead")
    main._authorize_case.assert_called_once()
    assert main.log_action.call_args.kwargs["resource"] == "case:C-1" and main.log_action.call_args.kwargs["justification"] == "witness lead"
    from fastapi import HTTPException
    main._authorize_case.side_effect = HTTPException(status_code=428, detail="sensitive case: justification required")
    assert hunt(client, "investigator", case_id="C-2").status_code == 428    # not swallowed into a 200 "error" body


def test_unified_enroll_and_bulk_enrolments_stamp_jurisdiction_and_audit(api):
    client, faces, main = api
    fields = {"fir_id": "FIR-9", "accused": "Ramesh Kumar", "mobile": "9876543210", "aadhaar": "x", "dob": "1990-01-01", "history": "h", "prison": "p"}
    r = client.post("/api/v1/biometric/unified-enroll", data=fields, files={"file": ("f.jpg", b"\xff\xd8\xffIMG", "image/jpeg")}, headers=_auth_headers("investigator"))
    assert r.json()["status"] == "success" and faces.registry[-1]["jurisdiction"] == "Test" and faces.registry[-1]["fir_id"] == "FIR-9"
    assert main.log_action.call_args.kwargs["action"] == "unified_enroll" and main.log_action.call_args.kwargs["extra"]["jurisdiction"] == "Test"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a__x.jpg", b"\xff\xd8\xffA")
    r = client.post("/api/v1/biometric/bulk-upload-zip", files={"file": ("p.zip", buf.getvalue(), "application/zip")}, headers=_auth_headers("investigator"))
    assert r.json()["status"] == "success" and faces.registry[-1]["jurisdiction"] == "Test"
    last = main.log_action.call_args.kwargs
    assert last["action"] == "face_bulk_enroll" and last["extra"]["enrolled"] == 1 and len(last["extra"]["zip_sha256"]) == 64
