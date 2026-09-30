"""WSRS endpoints (phase 3). Scores are computed and stored by wsrs.py; these routes only read them, plus an admin recompute."""

from __future__ import annotations

import json
import logging
from typing import Optional

from elasticsearch import helpers as es_helpers
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

import platform_api
import wsrs
from auth_security import get_current_user, require_role

logger = logging.getLogger("argus.wsrs")
router = APIRouter(prefix="/api/v1/analytics", tags=["wsrs"])


def _fail(exc: Exception) -> HTTPException:
    logger.exception("wsrs endpoint failure: %s", exc)
    return HTTPException(status_code=503, detail="An internal error occurred. Contact your system administrator.")


@router.get("/wsrs")
async def person_wsrs(person_name: str, current_user: dict = Depends(get_current_user)):
    """Stored WSRS with its factor breakdown for one person. Scoped callers need an in-jurisdiction FIR to see it."""
    scope = platform_api.search_scope(current_user)
    try:
        with platform_api._neo4j.session() as session:
            row = session.run(
                """
                MATCH (s:Suspect {id: $name})
                OPTIONAL MATCH (s)-[:LINKED_TO_FIR]->(f:FIR)
                WHERE $scope IS NULL OR f.jurisdiction = $scope
                RETURN s.wsrs_breakdown AS breakdown, s.wsrs_updated AS updated, count(f) AS in_scope
                """,
                name=person_name, scope=scope,
            ).single()
    except Exception as exc:
        raise _fail(exc) from exc
    platform_api.log_action(current_user, action="view_wsrs", resource=f"person:{person_name[:80]}", extra={"jurisdiction_filter": scope})
    if not row or not row["in_scope"] or not row["breakdown"]:
        return {"status": "success", "person": person_name, "wsrs": None, "message": "No women-safety risk score for this person."}
    return {"status": "success", "person": person_name, "wsrs": json.loads(row["breakdown"]), "computed_at": str(row["updated"])}


@router.get("/wsrs-leaderboard")
async def wsrs_leaderboard(
    limit: int = Query(default=20, ge=1, le=100),
    tier: Optional[str] = Query(default=None, pattern="^(HIGH|MEDIUM|LOW)$"),
    jurisdiction: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_role("supervisor", "admin")),
):
    """Top suspects by WSRS. A suspect belongs to a jurisdiction when they have a FIR there."""
    scope = platform_api.search_scope(current_user) or jurisdiction
    try:
        with platform_api._neo4j.session() as session:
            rows = list(session.run(
                """
                MATCH (s:Suspect) WHERE s.wsrs_score IS NOT NULL AND ($tier IS NULL OR s.wsrs_tier = $tier)
                  AND ($scope IS NULL OR EXISTS { (s)-[:LINKED_TO_FIR]->(:FIR {jurisdiction: $scope}) })
                RETURN s.id AS suspect, s.wsrs_score AS score, s.wsrs_tier AS tier, s.wsrs_breakdown AS breakdown
                ORDER BY s.wsrs_score DESC LIMIT $limit
                """,
                scope=scope, tier=tier, limit=limit,
            ))
    except Exception as exc:
        raise _fail(exc) from exc
    platform_api.log_action(current_user, action="view_wsrs_leaderboard", resource="analytics",
                            extra={"jurisdiction_filter": scope, "results": len(rows)})
    return {
        "status": "success",
        "jurisdiction": scope,
        "suspects": [{"suspect": r["suspect"], "score": r["score"], "tier": r["tier"], "wsrs": json.loads(r["breakdown"])} for r in rows],
        "label": wsrs.LEAD_LABEL,
    }


def _flag_ws_text_firs(session) -> int:
    """Backfill FIR.ws_text (and station) from Elasticsearch for FIRs ingested before WSRS existed."""
    n = 0
    for hit in es_helpers.scan(platform_api._es, index="argus-firs", query={"query": {"match_all": {}}},
                               _source=["fir_id", "network", "description", "station"]):
        src = hit["_source"]
        session.run("MATCH (f:FIR {id: $id}) SET f.ws_text = $ws, f.station = coalesce(f.station, $station)",
                    id=src.get("fir_id") or hit["_id"], ws=wsrs.is_ws_text(src), station=src.get("station"))
        n += 1
    return n


def _recompute_all() -> dict:
    with platform_api._neo4j.session() as session:
        flagged = _flag_ws_text_firs(session)
        results = wsrs.recompute(session)
    return {"firs_scanned": flagged, "suspects_scored": len(results), "high": sum(1 for r in results.values() if r["tier"] == "HIGH")}


@router.post("/wsrs/recompute")
async def wsrs_recompute(current_user: dict = Depends(require_role("admin"))):
    """Admin: rebuild every WSRS from the graph (also refreshes recency, which drifts with time)."""
    try:
        summary = await run_in_threadpool(_recompute_all)
    except Exception as exc:
        raise _fail(exc) from exc
    platform_api.log_action(current_user, action="wsrs_recompute", resource="analytics:wsrs", extra=summary)
    return {"status": "success", **summary}
