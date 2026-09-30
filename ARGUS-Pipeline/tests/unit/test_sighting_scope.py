"""Logging a sighting: scoped to the officer's jurisdiction, no phantom suspects, one sortable timestamp format."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import platform_api as pa


def user(role="investigator", jurisdiction="Central District"):
    return {"full_name": "D. Iyer", "role": role, "jurisdiction": jurisdiction}


def event(**kw):
    base = dict(suspect_name="Ramesh Kumar", camera_id="CAM-7", zone="Saket", timestamp="2026-09-30T10:00:00+05:30")
    base.update(kw)
    return pa.SurveillanceEvent(**base)


@pytest.fixture
def graph(monkeypatch):
    state = {"record": {"suspect": "Ramesh Kumar", "current_zone": "Saket", "current_time": "t", "firs": ["FIR-1"], "previous": []}, "calls": []}
    session = MagicMock()

    def run(query, **kw):
        state["calls"].append((query, kw))
        result = MagicMock()
        result.single.return_value = state["record"]
        result.__iter__ = lambda self: iter([])
        return result

    session.run.side_effect = run
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    monkeypatch.setattr(pa, "_neo4j", driver)
    monkeypatch.setattr(pa, "log_action", MagicMock())
    return state


def test_timestamp_is_normalised_to_utc():
    assert event(timestamp="2026-09-30T10:00:00+05:30").timestamp == "2026-09-30T04:30:00Z"
    assert event(timestamp="2026-09-30T04:30:00Z").timestamp == "2026-09-30T04:30:00Z"


@pytest.mark.parametrize("bad", ["yesterday", "2026-09-30 10:00", "2026-09-30T10:00:00", "30/09/2026 10:00"])
def test_timestamp_without_offset_or_not_iso_is_refused(bad):
    with pytest.raises(ValidationError):
        event(timestamp=bad)


def test_future_timestamp_is_refused():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with pytest.raises(ValidationError):
        event(timestamp=future)


def test_unknown_or_out_of_scope_suspect_is_404_and_creates_nothing(graph):
    graph["record"] = None
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pa.surveillance_event(event(), current_user=user()))
    assert exc.value.status_code == 404 and exc.value.detail == "Unknown suspect."
    query, kw = graph["calls"][0]
    assert "MERGE (s:Suspect" not in query                      # a typo must not create a person
    assert kw["scope"] == "Central District"                    # the jurisdiction reached the query...
    assert "$scope IS NULL OR" in query and "LINKED_TO_FIR" in query   # ...and filters the suspect


def test_unscoped_roles_are_not_filtered(graph):
    asyncio.run(pa.surveillance_event(event(), current_user=user("admin")))
    assert graph["calls"][0][1]["scope"] is None


def test_sighting_records_who_entered_it_and_is_not_labelled_synthetic(graph):
    asyncio.run(pa.surveillance_event(event(), current_user=user()))
    query, kw = graph["calls"][0]
    assert kw["entered_by"] == "D. Iyer" and "entered_by" in query
    assert "Synthetic" not in query


def test_audit_entry_names_the_suspect_and_time(graph):
    asyncio.run(pa.surveillance_event(event(), current_user=user()))
    extra = pa.log_action.call_args.kwargs["extra"]
    assert extra["suspect"] == "Ramesh Kumar" and extra["seen_at"] == "2026-09-30T04:30:00Z"


def test_fir_ids_in_the_alert_come_from_the_scoped_query(graph):
    asyncio.run(pa.surveillance_event(event(), current_user=user()))
    assert "f.jurisdiction = $scope" in graph["calls"][0][0]    # another jurisdiction's FIR ids never reach a scoped officer


def test_sighting_list_is_scoped(graph):
    asyncio.run(pa.surveillance_sightings(current_user=user()))
    query, kw = graph["calls"][0]
    assert kw["scope"] == "Central District" and "LINKED_TO_FIR" in query
