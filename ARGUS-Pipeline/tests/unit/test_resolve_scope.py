"""Entity resolution is jurisdiction-scoped, and the WSRS leaderboard carries what its page needs."""

import asyncio
import difflib
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import platform_api as pa
from tests.unit.test_platform_critical_path import _auth_headers


def user(role, jurisdiction="Central District", name="Test Officer"):
    return {"full_name": name, "role": role, "jurisdiction": jurisdiction}


@pytest.fixture
def graph(monkeypatch):
    """A fake Neo4j whose Suspect query returns the names in `visible`, remembering the scope it was called with."""
    from rapidfuzz import fuzz

    monkeypatch.setattr(fuzz, "ratio", lambda a, b: 100 * difflib.SequenceMatcher(None, a, b).ratio())
    state = {"names": ["Ramesh Kumar"], "scopes": [], "in_scope": True}
    session = MagicMock()

    def run(query, **kw):
        state["scopes"].append(kw.get("scope"))
        state["last_query"] = query
        result = MagicMock()
        result.__iter__ = lambda self: iter([{"name": n} for n in state["names"]])
        result.single.return_value = {"n": 1, "value": "9876543210"}
        return result

    session.run.side_effect = run
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(pa, "_neo4j", driver)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    monkeypatch.setattr(pa, "node_in_scope", lambda s, node, scope: state["in_scope"])
    return state, session


def test_check_only_considers_suspects_in_the_callers_jurisdiction(graph):
    state, _ = graph
    out = asyncio.run(pa.resolve_check("Ramesh Kumaar", current_user=user("investigator")))
    assert out and out[0]["candidate"] == "Ramesh Kumar"
    assert state["scopes"][-1] == "Central District"                         # the scope reached the query...
    assert "EXISTS" in state["last_query"] and "LINKED_TO_FIR" in state["last_query"] and "$scope IS NULL" in state["last_query"]   # ...and filters it
    asyncio.run(pa.resolve_check("Ramesh Kumaar", current_user=user("admin")))
    assert state["scopes"][-1] is None                                        # unscoped roles see everyone


def test_phone_lookup_is_scoped_too(graph):
    state, _ = graph
    state["in_scope"] = False
    assert asyncio.run(pa.resolve_check("9876543210", current_user=user("investigator"))) == []       # someone else's handset answers like an unknown number
    state["in_scope"] = True
    hit = asyncio.run(pa.resolve_check("9876543210", current_user=user("investigator")))
    assert hit and hit[0]["match_type"] == "exact_phone"


def test_decision_requires_both_names_to_be_visible_to_a_scoped_officer(graph, monkeypatch):
    state, session = graph
    cur = MagicMock()
    cur.fetchone.return_value = {"decision_id": "d1", "decided_at": datetime(2026, 9, 30)}
    monkeypatch.setattr(pa, "get_cursor", lambda **kw: MagicMock(__enter__=MagicMock(return_value=cur), __exit__=MagicMock(return_value=False)))
    req = pa.ResolutionDecisionRequest(name="Ramesh", candidate="Ramesh Kumar", decision="confirm_merge", similarity=0.9)
    state["in_scope"] = False
    with pytest.raises(HTTPException) as err:
        asyncio.run(pa.resolve_decision(req, current_user=user("investigator")))
    assert err.value.status_code == 404 and err.value.detail == "Unknown name."
    assert not any("ALIAS_OF" in str(c) for c in session.run.call_args_list)          # nothing was linked, nothing recorded
    state["in_scope"] = True
    out = asyncio.run(pa.resolve_decision(req, current_user=user("investigator")))
    assert out["decision"] == "confirm_merge" and out["graph_linked"] is True
    state["in_scope"] = False
    assert asyncio.run(pa.resolve_decision(req, current_user=user("admin")))["decision"] == "confirm_merge"    # unscoped roles are not restricted


def test_decision_history_is_own_only_for_scoped_officers_and_honours_limit(monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = []
    monkeypatch.setattr(pa, "get_cursor", lambda **kw: MagicMock(__enter__=MagicMock(return_value=cur), __exit__=MagicMock(return_value=False)))
    asyncio.run(pa.list_resolution_decisions(limit=10, current_user=user("investigator", name="Rahul Verma")))
    sql, params = cur.execute.call_args.args
    assert "decided_by = %s" in sql and params == ("Rahul Verma", 10)
    asyncio.run(pa.list_resolution_decisions(limit=10, current_user=user("supervisor")))
    sql, params = cur.execute.call_args.args
    assert "decided_by" not in sql and params == (10,)
    client = TestClient(__import__("main").app)
    assert client.get("/api/v1/resolve/decisions", params={"limit": 0}, headers=_auth_headers("admin")).status_code == 422
    assert client.get("/api/v1/resolve/decisions", params={"limit": 101}, headers=_auth_headers("admin")).status_code == 422


def test_leaderboard_returns_fir_count_jurisdictions_and_computed_at(monkeypatch):
    import main
    session = MagicMock()
    session.run.return_value = [{"suspect": "A", "score": 80.0, "tier": "HIGH", "breakdown": json.dumps({"total": 80}), "fir_count": 4,
                                 "jurisdictions": ["West Range", "Central District", None], "updated": "2026-09-30T10:00:00Z"}]
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(pa, "_neo4j", driver)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    body = TestClient(main.app).get("/api/v1/analytics/wsrs-leaderboard", headers=_auth_headers("supervisor")).json()
    row = body["suspects"][0]
    assert row["fir_count"] == 4 and row["jurisdictions"] == ["Central District", "West Range"] and row["computed_at"] == "2026-09-30T10:00:00Z"
    assert body["computed_at"] == "2026-09-30T10:00:00Z" and row["suspect"] == "A" and row["score"] == 80.0
    session.run.return_value = [{"suspect": "B", "score": 50.0, "tier": "MEDIUM", "breakdown": "{}"}]                # older rows without the new fields still work
    row = TestClient(main.app).get("/api/v1/analytics/wsrs-leaderboard", headers=_auth_headers("supervisor")).json()["suspects"][0]
    assert row["fir_count"] == 0 and row["jurisdictions"] == [] and row["computed_at"] is None
