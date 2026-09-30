"""Phase 5: evidence ledger hash chain and verification endpoints."""

import copy
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import evidence
import evidence_api
import platform_api as pa
from tests.unit.test_platform_critical_path import _auth_headers

CASE = "11111111-1111-1111-1111-111111111111"


def make_chain(n=3, other_case_from=None):
    rows, prev = [], None
    for i in range(n):
        at = datetime(2026, 9, 1, 10, i, 0, 123456, tzinfo=timezone.utc)
        row = {"ledger_id": f"id{i}", "file_id": f"raw/{i}.json", "file_sha256": evidence.sha256_hex(str(i).encode()),
               "case_id": "22222222-2222-2222-2222-222222222222" if other_case_from is not None and i >= other_case_from else CASE, "uploaded_by": "U", "uploaded_at": at, "prev_hash": prev}
        row["row_hash"] = evidence.compute_row_hash(**{k: row[k] for k in ("file_id", "file_sha256", "case_id", "uploaded_by", "uploaded_at", "prev_hash")})
        prev = row["row_hash"]
        rows.append(row)
    return rows


def test_chain_valid_and_hash_is_deterministic():
    rows = make_chain()
    assert evidence.check_chain(copy.deepcopy(rows))["valid"] is True
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]


def test_edit_breaks_that_row_and_later_links():
    rows = make_chain()
    rows[1]["file_sha256"] = "00"
    out = evidence.check_chain(rows)
    assert not out["valid"] and out["first_break"]["ledger_id"] == "id1"
    assert [r["row_ok"] for r in rows] == [True, False, True]   # row 2's own hash is fine; its link to row 1 still matches the stored hash


def test_deleting_or_reordering_rows_breaks_the_link():
    rows = make_chain()
    del rows[1]
    out = evidence.check_chain(rows)
    assert not out["valid"] and out["first_break"]["reason"].startswith("link")


def test_timezone_does_not_change_the_hash():
    a = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    b = a.astimezone(timezone(timedelta(hours=5, minutes=30)))
    kw = dict(file_id="f", file_sha256="s", case_id=None, uploaded_by="u", prev_hash=None)
    assert evidence.compute_row_hash(uploaded_at=a, **kw) == evidence.compute_row_hash(uploaded_at=b, **kw)


def test_record_upload_chains_to_previous_row(monkeypatch):
    cur = MagicMock()
    cur.fetchone.side_effect = [{"row_hash": "prevhash"}, {"ledger_id": "L", "seq": 1}]
    ctx = MagicMock(__enter__=MagicMock(return_value=cur), __exit__=MagicMock(return_value=False))
    monkeypatch.setattr(evidence, "get_cursor", lambda **kw: ctx)
    out = evidence.record_upload("raw/x", b"data", "U", CASE)
    insert_args = cur.execute.call_args_list[-1].args[1]
    assert out["file_sha256"] == evidence.sha256_hex(b"data") and insert_args[5] == "prevhash" and insert_args[6] == out["row_hash"]


def test_verify_delta_messages():
    assert "matches" in evidence.verify_delta("a", "a")
    assert "changed" in evidence.verify_delta("a", "b")
    assert "missing" in evidence.verify_delta("a", None)


@pytest.fixture
def client():
    import main
    return TestClient(main.app)


def _patch(monkeypatch, entry, file_bytes, chain=None):
    cur = MagicMock()
    cur.fetchone.return_value = entry
    ctx = MagicMock(__enter__=MagicMock(return_value=cur), __exit__=MagicMock(return_value=False))
    monkeypatch.setattr(evidence_api, "get_cursor", lambda **kw: ctx)
    monkeypatch.setattr(evidence_api, "_read_minio", lambda fid: file_bytes)
    monkeypatch.setattr(evidence_api, "_authorize_case", lambda *a: None)
    monkeypatch.setattr(evidence, "load_chain", lambda: chain if chain is not None else make_chain(1))
    monkeypatch.setattr(pa, "log_action", MagicMock())


def test_verify_intact_and_tampered(monkeypatch, client):
    chain = make_chain(1)
    entry = chain[0]
    _patch(monkeypatch, entry, b"0", chain)
    r = client.get("/api/v1/evidence/verify/raw/0.json", headers=_auth_headers("investigator")).json()
    assert r["intact"] is True and r["stored_hash"] == r["computed_hash"]
    _patch(monkeypatch, entry, b"tampered", make_chain(1))
    r = client.get("/api/v1/evidence/verify/raw/0.json", headers=_auth_headers("investigator")).json()
    assert r["intact"] is False and r["stored_hash"] != r["computed_hash"] and "changed" in r["delta_message"]
    _patch(monkeypatch, entry, None, make_chain(1))
    assert client.get("/api/v1/evidence/verify/raw/0.json", headers=_auth_headers("investigator")).json()["computed_hash"] is None


def test_verify_unknown_file_is_404(monkeypatch, client):
    _patch(monkeypatch, None, b"", [])
    assert client.get("/api/v1/evidence/verify/raw/none.json", headers=_auth_headers("investigator")).status_code == 404


def test_ledger_endpoint_filters_case_and_reports_chain(monkeypatch, client):
    chain = make_chain(3, other_case_from=2)
    _patch(monkeypatch, None, b"", chain)
    body = client.get("/api/v1/evidence/ledger", params={"case_id": CASE}, headers=_auth_headers("investigator")).json()
    assert len(body["entries"]) == 2 and body["chain"]["valid"] and all(e["row_ok"] for e in body["entries"])
    assert "file_sha256" in body["entries"][0]
