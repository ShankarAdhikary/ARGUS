"""Predictive-geography endpoints (phase 4): KDE hotspots and jurisdiction risk forecast."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

import geo_risk
import geo_store
import platform_api
from auth_security import get_current_user, require_role
from indic_text import WS_CATEGORIES

logger = logging.getLogger("argus.geo")
router = APIRouter(prefix="/api/v1/analytics", tags=["geography"])


def _fail(exc: Exception) -> HTTPException:
    logger.exception("geo endpoint failure: %s", exc)
    return HTTPException(status_code=503, detail="An internal error occurred. Contact your system administrator.")


def _fir_points(category: str, scope: Optional[str]) -> list[tuple[float, float]]:
    return geo_store.fir_points(category, scope)


@router.get("/hotspots")
async def hotspots(
    category: str = Query(default="WOMEN_SAFETY", pattern="^[A-Z_]{3,40}$"),
    current_user: dict = Depends(get_current_user),
):
    """Kernel-density hotspots of FIR locations as GeoJSON polygons at high / medium / low density."""
    scope = platform_api.search_scope(current_user)
    try:
        points = await run_in_threadpool(_fir_points, category, scope)
        collection = await run_in_threadpool(geo_risk.hotspots, points, 60, 0.25, category)
    except Exception as exc:
        raise _fail(exc) from exc
    collection["properties"].update({
        "jurisdiction_filter": scope,
        "explanation": "Density of FIR locations (Gaussian KDE). Coordinates are station/district centroids unless the FIR carried "
                       "a point, so hotspots show where reports are filed, not where offences happened.",
        "confidence": round(min(0.9, 0.3 + 0.03 * len(points)), 2),
    })
    platform_api.log_action(current_user, action="view_hotspots", resource=f"analytics:hotspots:{category}",
                            extra={"jurisdiction_filter": scope, "points": len(points)})
    return collection


def _jurisdiction_graph() -> dict:
    """suspects and women-safety FIR counts per jurisdiction, plus each suspect's WSRS, from the graph."""
    with platform_api._neo4j.session() as session:
        rows = list(session.run(
            """
            MATCH (s:Suspect)-[:LINKED_TO_FIR]->(f:FIR) WHERE f.jurisdiction IS NOT NULL
            RETURN s.id AS suspect, f.jurisdiction AS j, f.id AS fir, s.wsrs_score AS wsrs,
                   (coalesce(f.ws_text, false) OR EXISTS { (c:LegalCharge)-[:IN_FIR]->(f) WHERE c.offense_category IN $ws }) AS is_ws
            """,
            ws=sorted(WS_CATEGORIES),
        ))
    suspects: dict[str, set[str]] = defaultdict(set)
    ws_firs: dict[str, set[str]] = defaultdict(set)
    wsrs: dict[str, list[float]] = defaultdict(list)
    seen = set()
    for r in rows:
        suspects[r["j"]].add(r["suspect"])
        if r["is_ws"]:
            ws_firs[r["j"]].add(r["fir"])
        if r["wsrs"] is not None and (r["j"], r["suspect"]) not in seen:
            wsrs[r["j"]].append(r["wsrs"])
        seen.add((r["j"], r["suspect"]))
    return {"suspects": suspects, "ws_firs": ws_firs, "wsrs": wsrs}


def jurisdiction_risk(graph: dict) -> dict[str, float]:
    """0-100 risk per jurisdiction: half relative women-safety FIR volume, half the mean WSRS of its suspects."""
    peak = max((len(v) for v in graph["ws_firs"].values()), default=0)
    out = {}
    for j in graph["suspects"]:
        volume = 100.0 * len(graph["ws_firs"].get(j, ())) / peak if peak else 0.0
        scores = graph["wsrs"].get(j, [])
        out[j] = 0.5 * volume + 0.5 * (sum(scores) / len(scores) if scores else 0.0)
    return out


def _geographic_neighbours(jurisdiction: str) -> Optional[set[str]]:
    """District names bordering `jurisdiction` if it names a real district; None when it does not (e.g. demo zones)."""
    adj = geo_risk.adjacency()
    keys = [k for k in adj if k.split("|")[1].lower() == jurisdiction.lower()]
    if len(keys) != 1:
        return None
    return {n.split("|")[1] for n in adj[keys[0]]}


def _forecast(jurisdiction: str, decay: float, top: int) -> dict:
    graph = _jurisdiction_graph()
    if jurisdiction not in graph["suspects"]:
        return {"found": False}
    risk = jurisdiction_risk(graph)
    mine = graph["suspects"][jurisdiction]
    shared = {j: len(mine & s) for j, s in graph["suspects"].items() if j != jurisdiction}
    forecast = geo_risk.diffuse_risk(jurisdiction, risk, shared, len(mine), decay=decay, adjacent=_geographic_neighbours(jurisdiction), top=top)
    return {"found": True, "source_risk": round(risk[jurisdiction], 1), "forecast": forecast, "suspects_in_source": len(mine)}


@router.get("/risk-forecast")
async def risk_forecast(
    jurisdiction: str,
    decay: float = Query(default=geo_risk.DECAY_ADJACENT, gt=0, le=1),
    current_user: dict = Depends(require_role("analyst", "supervisor", "admin")),
):
    """Which neighbouring jurisdictions inherit risk from `jurisdiction` through shared suspects (one-hop diffusion).

    Analyst and above only: the answer names other jurisdictions, which jurisdiction-scoped officers must not see.
    """
    try:
        result = await run_in_threadpool(_forecast, jurisdiction, decay, 5)
    except Exception as exc:
        raise _fail(exc) from exc
    platform_api.log_action(current_user, action="view_risk_forecast", resource=f"analytics:forecast:{jurisdiction[:80]}")
    if not result["found"]:
        raise HTTPException(status_code=404, detail="No data for that jurisdiction.")
    return {
        "status": "success",
        "jurisdiction": jurisdiction,
        "source_risk": result["source_risk"],
        "forecast": result["forecast"],
        "method": "risk[neighbour] += risk[source] × (shared_suspects / total_suspects) × decay",
        "confidence": round(min(0.85, 0.3 + 0.05 * result["suspects_in_source"]), 2),
        "label": geo_risk.LEAD_LABEL,
    }
