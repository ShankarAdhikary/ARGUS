"""Postgres row-level security, checked directly against the database (no API in between).

Needs the live stack with the demo cases. Proves the policies work even if application code forgets to filter.
"""

from __future__ import annotations

import os

import pytest

if not os.getenv("ARGUS_LIVE_TESTS"):
    pytest.skip("Set ARGUS_LIVE_TESTS=1 to run against the live synthetic stack.", allow_module_level=True)

import db
from db import get_cursor
from request_context import clear_request_user, set_request_user

ADMIN = {"role": "admin", "jurisdiction": "National"}
INVESTIGATOR = {"role": "investigator", "jurisdiction": "Central District"}


@pytest.fixture(scope="module", autouse=True)
def rls_ready():
    db.init_db()          # creates the restricted role + policies and switches the role on in this process
    assert db._rls_ready
    yield
    clear_request_user()


def _count(sql: str, user: dict | None = None) -> int:
    clear_request_user()
    if user:
        set_request_user(user)
    try:
        with get_cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()["n"]
    finally:
        clear_request_user()


def test_the_service_user_would_bypass_rls_without_the_role_switch() -> None:
    """Why the role switch exists: the app connects as a superuser, and superusers ignore policies."""
    with get_cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        assert cur.fetchone()["rolsuper"] is True


def test_visibility_follows_role_and_jurisdiction_with_no_where_clause() -> None:
    everything = _count("SELECT count(*) AS n FROM cases")
    assert everything > 2
    assert _count("SELECT count(*) AS n FROM cases", ADMIN) == everything
    assert _count("SELECT count(*) AS n FROM cases", {"role": "analyst", "jurisdiction": "State HQ"}) == everything
    own = _count("SELECT count(*) AS n FROM cases", INVESTIGATOR)
    assert 0 < own < everything
    assert _count("SELECT count(*) AS n FROM cases WHERE jurisdiction <> 'Central District'", INVESTIGATOR) == 0


def test_unknown_or_empty_context_fails_closed() -> None:
    assert _count("SELECT count(*) AS n FROM cases", {"role": "", "jurisdiction": ""}) == 0
    assert _count("SELECT count(*) AS n FROM cases", {"role": "investigator", "jurisdiction": "Nowhere"}) == 0


def test_notes_and_pinned_entities_inherit_the_case_policy() -> None:
    for table in ("case_notes", "case_entity_links"):
        assert _count(f"SELECT count(*) AS n FROM {table}", INVESTIGATOR) < _count(f"SELECT count(*) AS n FROM {table}", ADMIN)


def test_writes_are_policed_too() -> None:
    set_request_user(INVESTIGATOR)
    try:
        with pytest.raises(Exception, match="row-level security"):
            with get_cursor(commit=True) as cur:
                cur.execute("INSERT INTO cases (title, fir_number, jurisdiction) VALUES ('probe', 'X', 'Harbor Division')")
        with get_cursor(commit=True) as cur:
            cur.execute("UPDATE cases SET status = 'closed' WHERE jurisdiction = 'Harbor Division'")
            assert cur.rowcount == 0
    finally:
        clear_request_user()


def test_the_restricted_role_does_not_leak_to_the_next_query() -> None:
    _count("SELECT count(*) AS n FROM cases", INVESTIGATOR)
    assert _count("SELECT count(*) AS n FROM cases") == _count("SELECT count(*) AS n FROM cases", ADMIN)


def test_restricted_role_cannot_tamper_with_the_audit_log() -> None:
    set_request_user(ADMIN)
    try:
        with pytest.raises(Exception):
            with get_cursor(commit=True) as cur:
                cur.execute("DELETE FROM audit_log")
    finally:
        clear_request_user()


def test_a_scoped_officer_can_use_every_case_write_path() -> None:
    """Regression: pinning an entity inserts into a table with an auto-numbered id, which needs sequence rights."""
    set_request_user(INVESTIGATOR)
    try:
        with get_cursor(commit=True) as cur:
            cur.execute("INSERT INTO cases (title, fir_number, jurisdiction) VALUES ('RLS write probe', 'RLS-W', 'Central District') RETURNING case_id")
            case_id = cur.fetchone()["case_id"]
            cur.execute("INSERT INTO case_entity_links (case_id, entity_type, entity_value, linked_by) VALUES (%s, 'person', 'Probe Person', 'tester')", (case_id,))
            cur.execute("INSERT INTO case_notes (case_id, author, content) VALUES (%s, 'tester', 'probe note')", (case_id,))
        with get_cursor() as cur:
            cur.execute("SELECT (SELECT count(*) FROM case_entity_links WHERE case_id = %s) AS links, (SELECT count(*) FROM case_notes WHERE case_id = %s) AS notes", (case_id, case_id))
            assert dict(cur.fetchone()) == {"links": 1, "notes": 1}
        # A different district's officer cannot pin onto that case.
        set_request_user({"role": "investigator", "jurisdiction": "Harbor Division"})
        with pytest.raises(Exception, match="row-level security"):
            with get_cursor(commit=True) as cur:
                cur.execute("INSERT INTO case_entity_links (case_id, entity_type, entity_value, linked_by) VALUES (%s, 'person', 'Intruder', 'x')", (case_id,))
    finally:
        clear_request_user()
        with get_cursor(commit=True) as cur:      # trusted context: remove the probe
            cur.execute("DELETE FROM case_notes WHERE content = 'probe note'")
            cur.execute("DELETE FROM case_entity_links WHERE entity_value = 'Probe Person'")
            cur.execute("DELETE FROM cases WHERE title = 'RLS write probe'")
