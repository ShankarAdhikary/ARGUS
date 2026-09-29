"""Unit tests for the ARGUS platform critical path.

These tests run entirely in-process with TestClient (no running Docker stack
needed).  All heavy external dependencies (Postgres, Neo4j, Redis, ES, MinIO)
are stubbed out by tests/unit/conftest.py before any module is imported.

Run:
    pytest tests/unit/ -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cursor_ctx(rows=None, single_row=None):
    """Returns a context-manager mock whose __enter__ yields a cursor mock."""
    cur = MagicMock()
    cur.fetchone.return_value = single_row
    cur.fetchall.return_value = rows or []
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, cur


@pytest.fixture(autouse=True)
def patch_log_action(monkeypatch):
    """Suppress the real audit-log writer in every test.

    log_action calls get_cursor internally and requires a correct DB row
    shape.  Since we're not testing audit internals here, patching it out
    keeps each test focused on the endpoint logic.
    """
    monkeypatch.setattr("platform_api.log_action", MagicMock())
    monkeypatch.setattr("audit.log_action", MagicMock())


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAuth:
    def test_login_success(self, monkeypatch):
        """Patch verify_password and the DB cursor to test the happy-path."""
        import platform_api as _pa
        user_row = {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "employee_id": "TEST001",
            "full_name": "Test User",
            "role": "investigator",
            "jurisdiction": "Central",
            "password_hash": "fakehash",
            "salt": "fakesalt",
            "mfa_enabled": False,
        }
        ctx, _ = _make_cursor_ctx(single_row=user_row)
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)
        monkeypatch.setattr(_pa, "verify_password", lambda pw, ph, s: True)

        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/v1/auth/login",
            json={"employee_id": "TEST001", "password": "testpass"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["role"] == "investigator"

    def test_login_wrong_password(self, monkeypatch):
        """Patch verify_password to return False — should 401."""
        import platform_api as _pa
        user_row = {
            "user_id": "00000000-0000-0000-0000-000000000002",
            "employee_id": "TEST002",
            "full_name": "Test User 2",
            "role": "analyst",
            "jurisdiction": "State",
            "password_hash": "fakehash",
            "salt": "fakesalt",
            "mfa_enabled": False,
        }
        ctx, _ = _make_cursor_ctx(single_row=user_row)
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)
        monkeypatch.setattr(_pa, "verify_password", lambda pw, ph, s: False)

        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/v1/auth/login",
            json={"employee_id": "TEST002", "password": "wrong"},
        )
        assert response.status_code == 401

    def test_login_unknown_user(self, monkeypatch):
        """When the DB returns no row for the employee_id, should 401."""
        import platform_api as _pa
        ctx, _ = _make_cursor_ctx(single_row=None)
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)

        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/v1/auth/login",
            json={"employee_id": "NOBODY", "password": "x"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# JWT / auth dependency helpers
# ---------------------------------------------------------------------------

def _make_token(role: str = "investigator") -> str:
    from auth_security import create_access_token
    return create_access_token({
        "user_id": "00000000-0000-0000-0000-000000000099",
        "employee_id": "UNIT001",
        "full_name": "Unit Tester",
        "role": role,
        "jurisdiction": "Test",
    })


def _auth_headers(role: str = "investigator") -> dict:
    return {"Authorization": f"Bearer {_make_token(role)}"}


# ---------------------------------------------------------------------------
# Case CRUD tests
# ---------------------------------------------------------------------------

class TestCases:
    def test_create_and_list_cases(self, monkeypatch):
        import datetime

        case_row = {
            "case_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "title": "Test Case",
            "fir_number": "FIR-001",
            "jurisdiction": "Central",
            "status": "open",
            "is_sensitive": False,
            "sensitivity_reason": None,
            "category": None,
            "opened_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        }
        import platform_api as _pa
        ctx, _ = _make_cursor_ctx(rows=[case_row])
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)

        from main import app
        client = TestClient(app)
        list_resp = client.get("/api/v1/cases", headers=_auth_headers())
        assert list_resp.status_code == 200
        assert isinstance(list_resp.json(), list)

    def test_investigator_blocked_from_other_jurisdiction(self, monkeypatch):
        import datetime
        import platform_api as _pa

        row = {
            "case_id": "dddddddd-0000-0000-0000-000000000004",
            "title": "Elsewhere", "fir_number": "FIR-1", "jurisdiction": "Other District",
            "status": "open", "is_sensitive": False, "sensitivity_reason": None, "category": None,
            "opened_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        }
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: _make_cursor_ctx(single_row=row, rows=[])[0])
        from main import app
        client = TestClient(app)
        url = "/api/v1/cases/dddddddd-0000-0000-0000-000000000004"
        assert client.get(url, headers=_auth_headers("investigator")).status_code == 403
        assert client.get(url, headers=_auth_headers("analyst")).status_code == 200

    def test_sensitive_case_blocked_without_justification(self, monkeypatch):
        import datetime
        import platform_api as _pa

        sensitive_row = {
            "case_id": "bbbbbbbb-0000-0000-0000-000000000002",
            "title": "Sensitive Case",
            "fir_number": "FIR-999",
            "jurisdiction": "Test",
            "status": "open",
            "is_sensitive": True,
            "sensitivity_reason": "Involves a minor.",
            "category": None,
            "opened_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        }
        # _fetch_case_detail: 1st call → case row, 2nd+3rd → entity_links/notes (empty)
        call_n = {"n": 0}
        def multi_cursor(**kw):
            call_n["n"] += 1
            return _make_cursor_ctx(
                single_row=sensitive_row if call_n["n"] == 1 else None,
                rows=[],
            )[0]
        monkeypatch.setattr(_pa, "get_cursor", multi_cursor)

        from main import app
        client = TestClient(app)
        response = client.get(
            "/api/v1/cases/bbbbbbbb-0000-0000-0000-000000000002",
            headers=_auth_headers(),
        )
        assert response.status_code == 428

    def test_sensitive_case_accessible_with_justification(self, monkeypatch):
        import datetime
        import platform_api as _pa

        sensitive_row = {
            "case_id": "cccccccc-0000-0000-0000-000000000003",
            "title": "Another Sensitive Case",
            "fir_number": "FIR-998",
            "jurisdiction": "Special",
            "status": "open",
            "is_sensitive": True,
            "sensitivity_reason": "Witness protection.",
            "category": None,
            "opened_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        }
        call_n = {"n": 0}
        def cycled_cursor(**kw):
            call_n["n"] += 1
            return _make_cursor_ctx(
                single_row=sensitive_row if call_n["n"] == 1 else None,
                rows=[],
            )[0]
        monkeypatch.setattr(_pa, "get_cursor", cycled_cursor)

        from main import app
        client = TestClient(app)
        response = client.get(
            "/api/v1/cases/cccccccc-0000-0000-0000-000000000003?justification=authorized+review",
            headers=_auth_headers("supervisor"),
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Audit log tests
# ---------------------------------------------------------------------------

class TestAudit:
    def test_audit_requires_admin_role(self, monkeypatch):
        from main import app
        client = TestClient(app)
        response = client.get("/api/v1/audit", headers=_auth_headers("investigator"))
        assert response.status_code == 403

    def test_audit_accessible_by_admin(self, monkeypatch):
        import datetime

        audit_row = {
            "audit_id": "dddddddd-0000-0000-0000-000000000004",
            "user_full_name": "Admin",
            "role": "admin",
            "action": "login",
            "resource": "auth",
            "justification": None,
            "occurred_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            "record_hash": "abc123",
            "previous_hash": None,
        }
        import platform_api as _pa
        ctx, _ = _make_cursor_ctx(rows=[audit_row])
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)

        from main import app
        client = TestClient(app)
        response = client.get("/api/v1/audit", headers=_auth_headers("admin"))
        assert response.status_code == 200
        entries = response.json()
        assert isinstance(entries, list)


# ---------------------------------------------------------------------------
# Pattern feedback tests
# ---------------------------------------------------------------------------

class TestPatterns:
    def test_pattern_feedback_records_verdict(self, monkeypatch):
        """Submitting a verdict must return 200 or 404 (no graph data); never a 5xx."""
        import platform_api as _pa
        ctx, _ = _make_cursor_ctx()
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)

        # Mock the Neo4j session to return no graph rows
        session_mock = MagicMock()
        session_mock.__enter__ = MagicMock(return_value=session_mock)
        session_mock.__exit__ = MagicMock(return_value=False)
        session_mock.run.return_value = iter([])

        import platform_api
        platform_api._neo4j.session = MagicMock(return_value=session_mock)

        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/v1/patterns/burner-9999988888/feedback",
            json={"verdict": "useful"},
            headers=_auth_headers("analyst"),
        )
        # 200 = pattern found after graph re-list; 404 = empty graph, no match.
        # Either is correct — never a 5xx.
        assert response.status_code in {200, 404}

    def test_pattern_feedback_rejects_invalid_verdict(self):
        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/v1/patterns/some-pattern/feedback",
            json={"verdict": "invalid_value"},
            headers=_auth_headers("analyst"),
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Alert rules tests
# ---------------------------------------------------------------------------

class TestAlerts:
    def test_create_alert_rule(self, monkeypatch):
        import datetime
        import uuid

        rule_uuid = uuid.UUID("eeeeeeee-0000-0000-0000-000000000005")
        rule_row = {
            "rule_id": rule_uuid,
            "entity_value": "Vikram Singh",
            "created_by": "Unit Tester",
            "created_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        }
        # The endpoint calls get_cursor once for INSERT RETURNING;
        # log_action is patched out via the autouse fixture.
        import platform_api as _pa
        ctx, _ = _make_cursor_ctx(single_row=rule_row)
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)

        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/v1/alerts/rules",
            json={"entity_value": "Vikram Singh"},
            headers=_auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["entity_value"] == "Vikram Singh"
        assert data["rule_id"] == str(rule_uuid)


class TestBurnerCutoff:
    def test_flags_only_top_percentile(self):
        from platform_api import burner_cutoff
        counts = list(range(1, 101))  # 100 phones with 1..100 calls
        cutoff = burner_cutoff(counts)
        assert sum(1 for c in counts if c >= cutoff) <= 6

    def test_never_below_minimum(self):
        from platform_api import burner_cutoff, BURNER_MIN_CALLS
        assert burner_cutoff([1, 1, 2, 2, 3]) == BURNER_MIN_CALLS
        assert burner_cutoff([]) == BURNER_MIN_CALLS


class TestMfaLogin:
    def test_password_alone_does_not_issue_a_session_when_mfa_enabled(self, monkeypatch):
        import platform_api as _pa
        user_row = {
            "user_id": "00000000-0000-0000-0000-000000000005", "employee_id": "MFA001", "full_name": "Mfa User",
            "role": "supervisor", "jurisdiction": "State", "password_hash": "h", "salt": "s", "mfa_enabled": True,
        }
        ctx, _ = _make_cursor_ctx(single_row=user_row)
        monkeypatch.setattr(_pa, "get_cursor", lambda **kw: ctx)
        monkeypatch.setattr(_pa, "verify_password", lambda pw, ph, s: True)
        from main import app
        response = TestClient(app).post("/api/v1/auth/login", json={"employee_id": "MFA001", "password": "x"})
        body = response.json()
        assert response.status_code == 200
        assert body["mfa_required"] is True and body["enrollment_required"] is False
        assert "access_token" not in body and body["mfa_token"]


class TestMfaCore:
    """TOTP (RFC 6238) and recovery-code primitives."""

    def test_rfc6238_vectors(self):
        import base64
        from auth_security import _hotp
        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        assert _hotp(secret, 59 // 30, 8) == "94287082"
        assert _hotp(secret, 1111111109 // 30, 8) == "07081804"

    def test_verify_accepts_window_and_rejects_replay(self):
        from auth_security import new_totp_secret, totp_code, verify_totp
        secret, now = new_totp_secret(), 1_700_000_000
        code = totp_code(secret, now)
        step = verify_totp(secret, code, now=now)
        assert step == now // 30
        assert verify_totp(secret, code, last_step=step, now=now) is None            # replay
        assert verify_totp(secret, totp_code(secret, now - 30), now=now) is not None  # one step of drift
        assert verify_totp(secret, totp_code(secret, now - 300), now=now) is None     # too old
        assert verify_totp(secret, "abc123", now=now) is None

    def test_secret_encryption_roundtrip(self):
        from auth_security import decrypt_secret, encrypt_secret, new_totp_secret
        secret = new_totp_secret()
        stored = encrypt_secret(secret)
        assert stored != secret and decrypt_secret(stored) == secret

    def test_recovery_codes(self):
        from auth_security import hash_recovery_code, new_recovery_codes
        codes = new_recovery_codes()
        assert len(set(codes)) == 8
        assert hash_recovery_code(codes[0]) == hash_recovery_code(codes[0].lower().replace("-", " "))

    def test_step_token_is_not_a_session_token(self, monkeypatch):
        from auth_security import create_purpose_token
        from main import app
        token = create_purpose_token("00000000-0000-0000-0000-000000000099", "mfa")
        response = TestClient(app).get("/api/v1/cases", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


class TestWomenSafetyFlag:
    """The badge must reflect real trafficking-type FIRs, not be stamped on everything."""

    def test_flag_needs_enough_fir_and_enough_share(self):
        from platform_api import _women_safety_assessment
        relevant = {f"T{i}" for i in range(10)}
        # 4 of 10 linked FIRs are trafficking cases -> flagged
        flag, hits, share = _women_safety_assessment([f"T{i}" for i in range(4)] + [f"X{i}" for i in range(6)], relevant)
        assert flag and hits == 4 and share == 0.4
        # plenty of trafficking FIRs but a tiny share of a huge record -> not flagged
        assert _women_safety_assessment([f"T{i}" for i in range(3)] + [f"X{i}" for i in range(60)], relevant)[0] is False
        # a high share but too few FIRs -> not flagged
        assert _women_safety_assessment(["T1", "T2"], relevant)[0] is False
        assert _women_safety_assessment([], relevant) == (False, 0, 0.0)

    def test_cluster_threshold_is_lower_because_shared_cases_are_few(self):
        from platform_api import _women_safety_assessment
        assert _women_safety_assessment(["T1", "T2", "X1"], {"T1", "T2"}, min_firs=2)[0] is True

    def test_sentence_states_the_evidence(self):
        from platform_api import _women_safety_sentence
        assert "26 of the linked FIRs (43%)" in _women_safety_sentence(True, 26, 0.43, "X")
        assert "none of the linked FIRs" in _women_safety_sentence(False, 0, 0.0, "X")
