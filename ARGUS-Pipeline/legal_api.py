"""Legal-charge and repeat-victim endpoints (phase 2)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import platform_api
from auth_security import get_current_user, require_role
from legal_graph import co_charge_matrix, ensure_schema

logger = logging.getLogger("argus.legal")
router = APIRouter(prefix="/api/v1", tags=["legal"])

LEAD_LABEL = "investigative lead — verify before use"


def _fail(exc: Exception) -> HTTPException:
    logger.exception("legal endpoint failure: %s", exc)
    return HTTPException(status_code=503, detail="An internal error occurred. Contact your system administrator.")


def _charge_key(act: str, section: str) -> str:
    return f"{act} {section}"


@router.get("/network/charges")
async def person_charges(person_name: str, current_user: dict = Depends(get_current_user)):
    """Full charge history of one person as a subgraph: Suspect -> LegalCharge -> FIR."""
    scope = platform_api.search_scope(current_user)
    try:
        with platform_api._neo4j.session() as session:
            rows = list(session.run(
                """
                MATCH (s:Suspect {id: $name})-[:CHARGED_WITH]->(c:LegalCharge)-[:IN_FIR]->(f:FIR)
                WHERE $scope IS NULL OR f.jurisdiction = $scope
                RETURN c.charge_id AS charge_id, c.section AS section, c.act AS act, c.offense_category AS category,
                       c.date_filed AS date_filed, c.confidence AS confidence, f.id AS fir_id, f.jurisdiction AS jurisdiction
                ORDER BY c.date_filed, c.charge_id
                """,
                name=person_name, scope=scope,
            ))
    except Exception as exc:
        raise _fail(exc) from exc
    platform_api.log_action(current_user, action="view_charges", resource=f"person:{person_name[:80]}",
                            extra={"jurisdiction_filter": scope, "charges": len(rows)})
    nodes = {person_name: {"id": person_name, "type": "Suspect", "label": person_name}}
    edges = []
    for r in rows:
        nodes[r["charge_id"]] = {
            "id": r["charge_id"], "type": "LegalCharge", "label": f"{r['act']} {r['section']}",
            "section": r["section"], "act": r["act"], "offense_category": r["category"],
            "date_filed": r["date_filed"], "confidence": r["confidence"],
        }
        nodes.setdefault(r["fir_id"], {"id": r["fir_id"], "type": "FIR", "label": r["fir_id"], "jurisdiction": r["jurisdiction"]})
        edges.append({"source": person_name, "target": r["charge_id"], "type": "CHARGED_WITH"})
        edges.append({"source": r["charge_id"], "target": r["fir_id"], "type": "IN_FIR"})
    return {
        "status": "success",
        "person": person_name,
        "total_charges": len(rows),
        "nodes": list(nodes.values()) if rows else [],
        "edges": edges,
        "label": LEAD_LABEL,
        **({} if rows else {"message": "No charges found in your jurisdiction."}),
    }


@router.get("/analytics/charge-patterns")
async def charge_patterns(
    limit: int = Query(default=50, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """How often two IPC/BNS sections are charged together in the same FIR (co-charge frequency)."""
    scope = platform_api.search_scope(current_user)
    try:
        with platform_api._neo4j.session() as session:
            rows = list(session.run(
                """
                MATCH (a:LegalCharge)-[:IN_FIR]->(f:FIR)<-[:IN_FIR]-(b:LegalCharge)
                WHERE a.charge_id < b.charge_id AND ($scope IS NULL OR f.jurisdiction = $scope)
                  AND (a.act <> b.act OR a.section <> b.section)
                RETURN a.act AS a_act, a.section AS a_sec, b.act AS b_act, b.section AS b_sec, count(DISTINCT f) AS n
                ORDER BY n DESC
                LIMIT $limit
                """,
                scope=scope, limit=limit,
            ))
    except Exception as exc:
        raise _fail(exc) from exc
    merged: dict[tuple[str, str], int] = {}
    for r in rows:
        key = tuple(sorted((_charge_key(r["a_act"], r["a_sec"]), _charge_key(r["b_act"], r["b_sec"]))))
        merged[key] = merged.get(key, 0) + r["n"]
    pairs = sorted(({"a": a, "b": b, "count": n} for (a, b), n in merged.items()), key=lambda p: -p["count"])
    platform_api.log_action(current_user, action="view_charge_patterns", resource="analytics", extra={"jurisdiction_filter": scope})
    return {
        "status": "success",
        "pairs": pairs,
        **co_charge_matrix(pairs),
        "explanation": "Counts of FIRs in which both sections were charged together. Descriptive frequency only.",
        "label": LEAD_LABEL,
    }


@router.get("/analytics/repeat-victims")
async def repeat_victims(
    jurisdiction: Optional[str] = Query(default=None),
    min_repeat: int = Query(default=2, ge=2, le=50),
    current_user: dict = Depends(require_role("supervisor", "admin")),
):
    """Victims who appear in several FIRs. Supervisor and above only; aggregate and pseudonymous, no PII is stored or returned."""
    scope = platform_api.search_scope(current_user) or jurisdiction
    try:
        with platform_api._neo4j.session() as session:
            rows = list(session.run(
                """
                MATCH (v:Victim)
                WHERE v.repeat_count >= $min AND ($scope IS NULL OR v.jurisdiction = $scope)
                RETURN v.victim_id AS vid, v.repeat_count AS repeat_count, v.offense_categories AS cats, v.jurisdiction AS jurisdiction
                ORDER BY v.repeat_count DESC
                LIMIT 200
                """,
                min=min_repeat, scope=scope,
            ))
    except Exception as exc:
        raise _fail(exc) from exc
    by_category: dict[str, int] = {}
    for r in rows:
        for c in r["cats"] or []:
            by_category[c] = by_category.get(c, 0) + 1
    # Reading victim data is itself audited.
    platform_api.log_action(current_user, action="view_repeat_victims", resource="analytics:victims",
                            extra={"jurisdiction_filter": scope, "results": len(rows)})
    return {
        "status": "success",
        "jurisdiction": scope,
        "repeat_victim_count": len(rows),
        "offense_categories": [{"category": k, "victims": v} for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])],
        "victims": [
            {"victim_ref": r["vid"][:10], "repeat_count": r["repeat_count"], "offense_categories": r["cats"] or [], "jurisdiction": r["jurisdiction"]}
            for r in rows
        ],
        "explanation": f"Victims linked to at least {min_repeat} FIRs by hashed identifier. Identifiers are pseudonymous hashes, never raw IDs.",
        "label": LEAD_LABEL,
    }
