"""ARGUS platform track: auth, cases, audit, entity resolution, centrality,
patterns, alerts, reports, and admin — the API-contract endpoints frozen in
WORK_PLAN_24H.md that the frontend calls with a mock fallback until these
ship. Wired into `main.py` via `app.include_router(platform_router)`.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Literal, Optional

import networkx as nx
import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from neo4j import GraphDatabase
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from audit import log_action, verify_chain
from auth_security import (
    create_access_token,
    create_purpose_token,
    decode_purpose_token,
    decrypt_secret,
    encrypt_secret,
    get_current_user,
    hash_recovery_code,
    new_recovery_codes,
    new_totp_secret,
    provisioning_uri,
    require_role,
    verify_password,
    verify_totp,
)
from config import LOGIN_LOCKOUT_SECONDS, MFA_REQUIRED_ROLES, LOGIN_MAX_FAILURES, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT
from db import get_cursor

router = APIRouter(prefix="/api/v1")

_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, password=REDIS_PASSWORD)
_neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ---------------------------------------------------------------------------
# Alerts helper — called from here and from main.py's ingestion endpoints so
# every newly extracted entity is checked against active watch rules.
# ---------------------------------------------------------------------------

def check_and_fire_alerts(entity_values: list[str]) -> None:
    values = {v.strip().lower(): v.strip() for v in entity_values if v and v.strip()}
    if not values:
        return
    with get_cursor(commit=True) as cur:
        cur.execute("SELECT rule_id, entity_value FROM alert_rules")
        rules = cur.fetchall()
        for rule in rules:
            key = rule["entity_value"].strip().lower()
            if key in values:
                cur.execute(
                    """
                    INSERT INTO alerts (rule_id, entity_value, message)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        rule["rule_id"],
                        values[key],
                        f'Watched entity "{values[key]}" appeared in newly ingested data.',
                    ),
                )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    employee_id: str
    password: str


_login_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1, password=REDIS_PASSWORD)


def _lockout_key(employee_id: str) -> str:
    return f"argus:login_fail:{employee_id.lower()}"


@router.post("/auth/login")
async def login(payload: LoginRequest):
    # Brute-force protection. If Redis is unreachable we fail open (availability) but still audit.
    key = _lockout_key(payload.employee_id)
    try:
        failures = int(_login_redis.get(key) or 0)
    except Exception:
        failures = 0
    if failures >= LOGIN_MAX_FAILURES:
        log_action({"full_name": payload.employee_id, "role": "anonymous"}, action="login_locked_out", resource="auth")
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE employee_id = %s", (payload.employee_id,))
        user = cur.fetchone()
    if not user or not verify_password(payload.password, user["password_hash"], user["salt"]):
        try:
            if _login_redis.incr(key) == 1:
                _login_redis.expire(key, LOGIN_LOCKOUT_SECONDS)
        except Exception:
            pass
        log_action({"full_name": payload.employee_id, "role": "anonymous"}, action="login_failed", resource="auth")
        raise HTTPException(status_code=401, detail="Invalid employee ID or password.")
    try:
        _login_redis.delete(key)
    except Exception:
        pass
    if user["mfa_enabled"] or user["role"] in MFA_REQUIRED_ROLES:
        # Password accepted; a second factor is still needed before any session token is issued.
        log_action({"full_name": user["full_name"], "role": user["role"]}, action="login_password_ok_mfa_pending", resource="auth")
        return {
            "mfa_required": True,
            "enrollment_required": not user["mfa_enabled"],
            "mfa_token": create_purpose_token(str(user["user_id"]), "mfa"),
        }
    return _issue_session(user, action="login")


def _user_dict(user: dict) -> dict:
    return {
        "user_id": str(user["user_id"]),
        "employee_id": user["employee_id"],
        "full_name": user["full_name"],
        "role": user["role"],
        "jurisdiction": user["jurisdiction"],
    }


def _issue_session(user: dict, action: str, extra: Optional[dict] = None) -> dict:
    user_dict = _user_dict(user)
    token = create_access_token(user_dict)
    log_action(user_dict, action=action, resource="auth")
    return {"access_token": token, **user_dict, **(extra or {})}


# ---------------------------------------------------------------------------
# Multi-factor authentication (authenticator-app TOTP)
# ---------------------------------------------------------------------------

class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=16)


class MfaEnrollBeginRequest(BaseModel):
    mfa_token: str


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=16)


def _load_user(user_id: str) -> dict:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


def _mfa_guard(user: dict) -> str:
    """Shared brute-force limiter for second-factor attempts; returns the Redis key."""
    key = _lockout_key(user["employee_id"])
    try:
        if int(_login_redis.get(key) or 0) >= LOGIN_MAX_FAILURES:
            log_action(_user_dict(user), action="mfa_locked_out", resource="auth")
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    except HTTPException:
        raise
    except Exception:
        pass
    return key


def _mfa_failed(user: dict, key: str) -> None:
    try:
        if _login_redis.incr(key) == 1:
            _login_redis.expire(key, LOGIN_LOCKOUT_SECONDS)
    except Exception:
        pass
    log_action(_user_dict(user), action="mfa_failed", resource="auth")
    raise HTTPException(status_code=401, detail="Invalid verification code.")


def _check_second_factor(user: dict, code: str) -> str:
    """Accept a current TOTP code or a one-time recovery code. Returns 'totp' or 'recovery'."""
    key = _mfa_guard(user)
    step = verify_totp(decrypt_secret(user["mfa_secret"]), code, user["mfa_last_step"]) if user["mfa_secret"] else None
    if step is not None:
        with get_cursor(commit=True) as cur:
            # Only advances the step (rejects replays even across workers).
            cur.execute(
                "UPDATE users SET mfa_last_step = %s WHERE user_id = %s AND (mfa_last_step IS NULL OR mfa_last_step < %s) RETURNING 1",
                (step, user["user_id"], step),
            )
            if not cur.fetchone():
                _mfa_failed(user, key)
        return "totp"
    digest = hash_recovery_code(code)
    remaining = [h for h in (user["mfa_recovery"] or []) if h != digest]
    if len(remaining) != len(user["mfa_recovery"] or []):
        with get_cursor(commit=True) as cur:
            cur.execute("UPDATE users SET mfa_recovery = %s::jsonb WHERE user_id = %s", (json.dumps(remaining), user["user_id"]))
        return "recovery"
    _mfa_failed(user, key)
    raise AssertionError("unreachable")  # _mfa_failed always raises


def _clear_failures(user: dict) -> None:
    try:
        _login_redis.delete(_lockout_key(user["employee_id"]))
    except Exception:
        pass


def _begin_enrollment(user: dict) -> dict:
    secret = new_totp_secret()
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET mfa_secret = %s, mfa_enabled = false, mfa_last_step = NULL WHERE user_id = %s",
            (encrypt_secret(secret), user["user_id"]),
        )
    return {"secret": secret, "otpauth_uri": provisioning_uri(secret, user["employee_id"])}


def _complete_enrollment(user: dict, code: str) -> list[str]:
    if not user["mfa_secret"]:
        raise HTTPException(status_code=400, detail="Start enrolment first.")
    key = _mfa_guard(user)
    step = verify_totp(decrypt_secret(user["mfa_secret"]), code)
    if step is None:
        _mfa_failed(user, key)
    codes = new_recovery_codes()
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET mfa_enabled = true, mfa_last_step = %s, mfa_recovery = %s::jsonb WHERE user_id = %s",
            (step, json.dumps([hash_recovery_code(c) for c in codes]), user["user_id"]),
        )
    return codes


@router.post("/auth/mfa/verify")
async def mfa_verify(payload: MfaVerifyRequest):
    user = _load_user(decode_purpose_token(payload.mfa_token, "mfa"))
    if not user["mfa_enabled"]:
        raise HTTPException(status_code=400, detail="MFA is not enrolled for this account.")
    method = _check_second_factor(user, payload.code)
    _clear_failures(user)
    return _issue_session(user, action="login_mfa_recovery_code" if method == "recovery" else "login_mfa",
                          extra={"recovery_codes_left": len(user["mfa_recovery"] or []) - (1 if method == "recovery" else 0)})


@router.post("/auth/mfa/enroll/begin")
async def mfa_enroll_begin(payload: MfaEnrollBeginRequest):
    """First-time enrolment for a role that must use MFA (called with the password-step token)."""
    user = _load_user(decode_purpose_token(payload.mfa_token, "mfa"))
    if user["mfa_enabled"]:
        raise HTTPException(status_code=409, detail="MFA is already enrolled.")
    return _begin_enrollment(user)


@router.post("/auth/mfa/enroll/complete")
async def mfa_enroll_complete(payload: MfaVerifyRequest):
    user = _load_user(decode_purpose_token(payload.mfa_token, "mfa"))
    if user["mfa_enabled"]:
        raise HTTPException(status_code=409, detail="MFA is already enrolled.")
    codes = _complete_enrollment(user, payload.code)
    _clear_failures(user)
    return _issue_session(user, action="mfa_enrolled_login", extra={"recovery_codes": codes})


@router.get("/auth/mfa/status")
async def mfa_status(current_user: dict = Depends(get_current_user)):
    user = _load_user(current_user["user_id"])
    return {
        "enabled": user["mfa_enabled"],
        "required": user["role"] in MFA_REQUIRED_ROLES,
        "recovery_codes_left": len(user["mfa_recovery"] or []),
    }


@router.post("/auth/mfa/setup")
async def mfa_setup(current_user: dict = Depends(get_current_user)):
    user = _load_user(current_user["user_id"])
    if user["mfa_enabled"]:
        raise HTTPException(status_code=409, detail="MFA is already enabled. Disable it first to re-enrol.")
    return _begin_enrollment(user)


@router.post("/auth/mfa/enable")
async def mfa_enable(payload: MfaCodeRequest, current_user: dict = Depends(get_current_user)):
    user = _load_user(current_user["user_id"])
    if user["mfa_enabled"]:
        raise HTTPException(status_code=409, detail="MFA is already enabled.")
    codes = _complete_enrollment(user, payload.code)
    log_action(current_user, action="mfa_enabled", resource="auth")
    return {"enabled": True, "recovery_codes": codes}


@router.post("/auth/mfa/disable")
async def mfa_disable(payload: MfaCodeRequest, current_user: dict = Depends(get_current_user)):
    user = _load_user(current_user["user_id"])
    if user["role"] in MFA_REQUIRED_ROLES:
        raise HTTPException(status_code=403, detail="MFA is mandatory for your role and cannot be disabled.")
    if not user["mfa_enabled"]:
        raise HTTPException(status_code=409, detail="MFA is not enabled.")
    _check_second_factor(user, payload.code)
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET mfa_enabled = false, mfa_secret = NULL, mfa_last_step = NULL, mfa_recovery = '[]'::jsonb WHERE user_id = %s",
            (user["user_id"],),
        )
    log_action(current_user, action="mfa_disabled", resource="auth")
    return {"enabled": False}


@router.post("/admin/users/{employee_id}/mfa-reset")
async def admin_mfa_reset(employee_id: str, current_user: dict = Depends(require_role("admin"))):
    """Recover an officer who lost their authenticator and recovery codes. Audited; they must re-enrol."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET mfa_enabled = false, mfa_secret = NULL, mfa_last_step = NULL, mfa_recovery = '[]'::jsonb "
            "WHERE employee_id = %s RETURNING employee_id",
            (employee_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    _clear_failures({"employee_id": employee_id})
    log_action(current_user, action="mfa_reset_by_admin", resource=f"user:{employee_id}")
    return {"employee_id": employee_id, "mfa_enabled": False}


@router.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (current_user["user_id"],))
        user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Session no longer valid.")
    return {
        "user_id": str(user["user_id"]),
        "employee_id": user["employee_id"],
        "full_name": user["full_name"],
        "role": user["role"],
        "jurisdiction": user["jurisdiction"],
    }


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

class CaseCreateRequest(BaseModel):
    title: str
    fir_number: str
    jurisdiction: str
    is_sensitive: bool = False
    sensitivity_reason: Optional[str] = None
    category: Optional[str] = None


class CaseEntityRequest(BaseModel):
    entity_type: str
    entity_value: str


class CaseNoteRequest(BaseModel):
    content: str


def _case_summary_row(row: dict) -> dict:
    return {
        "case_id": str(row["case_id"]),
        "title": row["title"],
        "fir_number": row["fir_number"],
        "jurisdiction": row["jurisdiction"],
        "status": row["status"],
        "is_sensitive": row["is_sensitive"],
        "sensitivity_reason": row["sensitivity_reason"],
        "category": row["category"],
        "opened_at": row["opened_at"].isoformat(),
    }


# Roles with cross-jurisdiction oversight; everyone else is limited to their own jurisdiction (FR-10).
CROSS_JURISDICTION_ROLES = ("admin", "supervisor", "analyst")


def _assert_case_scope(detail: dict, current_user: dict) -> None:
    if current_user["role"] in CROSS_JURISDICTION_ROLES or detail["jurisdiction"] == current_user["jurisdiction"]:
        return
    log_action(current_user, action="denied_out_of_jurisdiction", resource=f"case:{detail['case_id']}")
    raise HTTPException(status_code=403, detail="This case belongs to another jurisdiction.")


@router.get("/cases")
async def list_cases(current_user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        if current_user["role"] in CROSS_JURISDICTION_ROLES:
            cur.execute("SELECT * FROM cases ORDER BY opened_at DESC")
        else:
            cur.execute("SELECT * FROM cases WHERE jurisdiction = %s ORDER BY opened_at DESC", (current_user["jurisdiction"],))
        rows = cur.fetchall()
    log_action(current_user, action="list_cases", resource="cases")
    return [_case_summary_row(r) for r in rows]


@router.post("/cases")
async def create_case(payload: CaseCreateRequest, current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in CROSS_JURISDICTION_ROLES and payload.jurisdiction != current_user["jurisdiction"]:
        raise HTTPException(status_code=403, detail="You can only open cases in your own jurisdiction.")
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO cases (title, fir_number, jurisdiction, is_sensitive, sensitivity_reason, category)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (payload.title, payload.fir_number, payload.jurisdiction, payload.is_sensitive, payload.sensitivity_reason, payload.category),
        )
        row = cur.fetchone()
    log_action(current_user, action="create_case", resource=f"case:{row['case_id']}")
    return _case_summary_row(row)


def _fetch_case_detail(case_id: str) -> dict:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM cases WHERE case_id = %s", (case_id,))
        case_row = cur.fetchone()
        if not case_row:
            raise HTTPException(status_code=404, detail="Case not found.")
        cur.execute("SELECT * FROM case_entity_links WHERE case_id = %s ORDER BY linked_at", (case_id,))
        entity_rows = cur.fetchall()
        cur.execute("SELECT * FROM case_notes WHERE case_id = %s ORDER BY created_at", (case_id,))
        note_rows = cur.fetchall()
    detail = _case_summary_row(case_row)
    detail["entities"] = [
        {
            "entity_type": r["entity_type"],
            "entity_value": r["entity_value"],
            "linked_by": r["linked_by"],
            "linked_at": r["linked_at"].isoformat(),
        }
        for r in entity_rows
    ]
    detail["notes"] = [
        {
            "note_id": str(r["note_id"]),
            "author": r["author"],
            "content": r["content"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in note_rows
    ]
    return detail


def enforce_case_access(case_id: Optional[str], justification: Optional[str], current_user: dict) -> None:
    if not case_id:
        return
    detail = _fetch_case_detail(case_id)
    _assert_case_scope(detail, current_user)
    if detail["is_sensitive"] and not justification:
        log_action(
            current_user,
            action="denied_sensitive_case_access",
            resource=f"case:{case_id}:graph",
        )
        raise HTTPException(
            status_code=428,
            detail="This case is marked sensitive. Provide ?justification= to access its graph.",
        )
    log_action(
        current_user,
        action="SENSITIVE_GRAPH_VIEW" if detail["is_sensitive"] else "view_case_graph",
        resource=f"case:{case_id}:graph",
        justification=justification,
    )


@router.get("/cases/{case_id}")
async def get_case(case_id: str, justification: Optional[str] = Query(default=None), current_user: dict = Depends(get_current_user)):
    detail = _fetch_case_detail(case_id)
    _assert_case_scope(detail, current_user)
    if detail["is_sensitive"] and not justification:
        log_action(current_user, action="denied_sensitive_case_access", resource=f"case:{case_id}")
        raise HTTPException(
            status_code=428,
            detail="This case is marked sensitive. Provide ?justification= to access it; the access will be audited.",
        )
    log_action(
        current_user,
        action="SENSITIVE_VIEW" if detail["is_sensitive"] else "view_case",
        resource=f"case:{case_id}",
        justification=justification,
    )
    return detail


@router.post("/cases/{case_id}/entities")
async def add_case_entity(case_id: str, payload: CaseEntityRequest, current_user: dict = Depends(get_current_user)):
    _assert_case_scope(_fetch_case_detail(case_id), current_user)
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO case_entity_links (case_id, entity_type, entity_value, linked_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (case_id, entity_type, entity_value) DO NOTHING
            """,
            (case_id, payload.entity_type, payload.entity_value, current_user["full_name"]),
        )
    log_action(current_user, action="pin_entity", resource=f"case:{case_id}")
    return _fetch_case_detail(case_id)


@router.post("/cases/{case_id}/notes")
async def add_case_note(case_id: str, payload: CaseNoteRequest, current_user: dict = Depends(get_current_user)):
    _assert_case_scope(_fetch_case_detail(case_id), current_user)
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO case_notes (case_id, author, content) VALUES (%s, %s, %s)",
            (case_id, current_user["full_name"], payload.content),
        )
    log_action(current_user, action="add_note", resource=f"case:{case_id}")
    return _fetch_case_detail(case_id)


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

@router.get("/resolve/check")
async def resolve_check(name: str, current_user: dict = Depends(get_current_user)):
    normalized = normalise_phone_identifier(name)
    if is_phone_identifier(name):
        with _neo4j.session() as session:
            exact_phone = session.run(
                "MATCH (p:Phone {id: $phone}) RETURN p.id AS value LIMIT 1",
                phone=normalized,
            ).single()
        if exact_phone:
            return [{
                "candidate": exact_phone["value"],
                "similarity": 1.0,
                "match_type": "exact_phone",
                "resolution": "review",
                "suggested": "merge",
            }]

    with _neo4j.session() as session:
        result = session.run("MATCH (s:Suspect) RETURN DISTINCT s.id AS name")
        candidates = [row["name"] for row in result if row["name"]]
    scored = []
    for candidate in candidates:
        if candidate.lower() == name.lower():
            continue
        similarity = fuzz.ratio(name.lower(), candidate.lower()) / 100.0
        if similarity > 0.5:
            rounded = round(similarity, 2)
            scored.append(
                {
                    "candidate": candidate,
                    "similarity": rounded,
                    "match_type": "fuzzy_name",
                    "resolution": "review",
                    "suggested": "merge" if rounded >= 0.92 else "possible_match",
                }
            )
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    log_action(current_user, action="resolve_check", resource=f"name:{name}")
    return scored[:10]


class ResolutionDecisionRequest(BaseModel):
    name: str
    candidate: str
    decision: str = Field(pattern="^(confirm_merge|reject)$")
    similarity: Optional[float] = None
    note: Optional[str] = None


@router.post("/resolve/decision")
async def resolve_decision(payload: ResolutionDecisionRequest, current_user: dict = Depends(require_role("investigator", "analyst", "supervisor", "admin"))):
    """Human-in-the-loop confirmation (FR-03). Confirming links the two identities
    with a reversible ALIAS_OF edge; nothing is ever destructively merged."""
    linked = False
    if payload.decision == "confirm_merge":
        with _neo4j.session() as session:
            record = session.run(
                """
                MATCH (a:Suspect {id: $name}), (b:Suspect {id: $candidate})
                MERGE (a)-[r:ALIAS_OF]->(b)
                SET r.confirmed_by = $by, r.confirmed_at = datetime(), r.similarity = $sim
                RETURN count(r) AS n
                """,
                name=payload.name, candidate=payload.candidate, by=current_user["full_name"], sim=payload.similarity,
            ).single()
            linked = bool(record and record["n"])
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO resolution_decisions (name, candidate, similarity, decision, decided_by, note)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING decision_id, decided_at
            """,
            (payload.name, payload.candidate, payload.similarity, payload.decision, current_user["full_name"], payload.note),
        )
        row = cur.fetchone()
    log_action(current_user, action=f"resolve_{payload.decision}", resource=f"name:{payload.name}->{payload.candidate}")
    return {
        "decision_id": str(row["decision_id"]),
        "decision": payload.decision,
        "graph_linked": linked,
        "decided_at": row["decided_at"].isoformat(),
    }


@router.get("/resolve/decisions")
async def list_resolution_decisions(current_user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM resolution_decisions ORDER BY decided_at DESC LIMIT 50")
        rows = cur.fetchall()
    return [
        {
            "decision_id": str(r["decision_id"]), "name": r["name"], "candidate": r["candidate"],
            "similarity": r["similarity"], "decision": r["decision"], "decided_by": r["decided_by"],
            "note": r["note"], "decided_at": r["decided_at"].isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Centrality
# ---------------------------------------------------------------------------

def _pagerank(graph: "nx.Graph") -> dict:
    """networkx's default PageRank requires scipy. Fall back to its pure-Python
    solver so the analytics endpoints stay available on installs where the
    scipy wheel is unavailable."""
    try:
        return nx.pagerank(graph)
    except ModuleNotFoundError:
        from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_python

        return _pagerank_python(graph)


_CENTRALITY_TTL = float(os.getenv("CENTRALITY_CACHE_SECONDS", "300"))
_BETWEENNESS_SAMPLE = int(os.getenv("BETWEENNESS_SAMPLE", "150"))
_centrality_cache: dict = {"at": 0.0, "rows": []}
_centrality_lock = threading.Lock()


def _compute_centrality() -> list[dict]:
    """Cached wrapper: the graph scan + centrality is expensive, so reuse it for a few minutes."""
    with _centrality_lock:
        if time.monotonic() - _centrality_cache["at"] < _CENTRALITY_TTL and _centrality_cache["rows"]:
            return _centrality_cache["rows"]
        rows = _compute_centrality_uncached()
        _centrality_cache.update(at=time.monotonic(), rows=rows)
        return rows


def _compute_centrality_uncached() -> list[dict]:
    graph = nx.Graph()
    with _neo4j.session() as session:
        for row in session.run("MATCH (s:Suspect)-[:LINKED_TO_FIR]->(f:FIR) RETURN s.id AS suspect, f.id AS fir"):
            if row["suspect"] and row["fir"]:
                graph.add_node(row["suspect"], entity_type="person")
                graph.add_node(f"FIR:{row['fir']}", entity_type="fir")
                graph.add_edge(row["suspect"], f"FIR:{row['fir']}")
        for row in session.run("MATCH (p1:Phone)-[:CALLED]->(p2:Phone) RETURN p1.id AS a, p2.id AS b"):
            if row["a"] and row["b"]:
                graph.add_node(row["a"], entity_type="phone")
                graph.add_node(row["b"], entity_type="phone")
                graph.add_edge(row["a"], row["b"])
        # Joins the two halves: without this the person/FIR records and the call
        # graph are separate components, and PageRank over people degenerates
        # into "who appears in the most FIRs".
        for row in session.run("MATCH (s:Suspect)-[:USES_PHONE]->(p:Phone) RETURN s.id AS suspect, p.id AS phone"):
            if row["suspect"] and row["phone"]:
                graph.add_node(row["suspect"], entity_type="person")
                graph.add_node(row["phone"], entity_type="phone")
                graph.add_edge(row["suspect"], row["phone"])

    if graph.number_of_nodes() == 0:
        return []

    pagerank = _pagerank(graph)
    # Exact betweenness is O(V*E); sample source nodes on large graphs (fixed seed = stable ranks).
    sample = _BETWEENNESS_SAMPLE if graph.number_of_nodes() > _BETWEENNESS_SAMPLE else None
    betweenness = nx.betweenness_centrality(graph, k=sample, seed=42)

    rows = []
    for node, attrs in graph.nodes(data=True):
        entity_type = attrs.get("entity_type")
        if entity_type not in ("person", "phone"):
            continue
        rows.append(
            {
                "entity_value": node,
                "entity_type": entity_type,
                "pagerank": round(pagerank.get(node, 0.0), 4),
                "betweenness": round(betweenness.get(node, 0.0), 4),
            }
        )
    rows.sort(key=lambda r: r["pagerank"], reverse=True)
    return rows


@router.get("/analytics/centrality")
async def analytics_centrality(current_user: dict = Depends(get_current_user)):
    rows = await run_in_threadpool(_compute_centrality)
    log_action(current_user, action="view_centrality", resource="analytics")
    return rows[:25]


# ---------------------------------------------------------------------------
# Communities (Louvain) and surveillance correlation
# ---------------------------------------------------------------------------

PHONE_PREFIXES = ("phone:", "phone/", "tel:", "msisdn:")


def normalise_phone_identifier(identifier: str) -> str:
    """Strips any source-system prefix and separators from a phone identifier.

    Ingested graph nodes carry bare numbers, but identifiers arriving from
    other systems may be namespaced ("phone:9999988888"). Comparing those two
    forms as raw strings silently fails to match, so normalise before use.
    """
    value = (identifier or "").strip()
    lowered = value.lower()
    for prefix in PHONE_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix):]
            break
    return "".join(ch for ch in value if ch.isdigit())


def is_phone_identifier(identifier: str) -> bool:
    """True when the identifier denotes a phone rather than a person. Detection
    is by prefix or by the value being all digits — never by substring search,
    which both misses bare numbers and misclassifies names containing 'phone'."""
    value = (identifier or "").strip()
    if value.lower().startswith(PHONE_PREFIXES):
        return True
    digits = normalise_phone_identifier(value)
    return bool(digits) and digits == "".join(value.split())


def _compute_communities() -> list[dict]:
    """Louvain modularity over the phone-call graph, to surface cells that talk
    to each other far more than they talk to the rest of the network."""
    graph = nx.Graph()
    with _neo4j.session() as session:
        for row in session.run("MATCH (p1:Phone)-[:CALLED]->(p2:Phone) RETURN p1.id AS a, p2.id AS b"):
            if row["a"] and row["b"]:
                graph.add_edge(row["a"], row["b"])

    if graph.number_of_nodes() == 0:
        return []

    communities = nx.community.louvain_communities(graph, seed=42)
    ranked = sorted(communities, key=len, reverse=True)
    return [
        {
            "cluster_id": index,
            "size": len(members),
            "members": sorted(members),
            "explanation": (
                f"Cluster {index} groups {len(members)} numbers that call each other far more "
                "than they call the rest of the network, consistent with a single operating cell."
            ),
        }
        for index, members in enumerate(ranked)
    ]


@router.get("/analytics/communities")
async def analytics_communities(current_user: dict = Depends(get_current_user)):
    clusters = await run_in_threadpool(_compute_communities)
    log_action(current_user, action="view_communities", resource="analytics")
    return clusters


class SurveillanceEvent(BaseModel):
    suspect_name: str = Field(min_length=1, max_length=200)
    camera_id: str = Field(min_length=1, max_length=100)
    zone: str = Field(min_length=1, max_length=200)
    timestamp: str = Field(min_length=1, max_length=64)
    match_confidence: float = Field(default=0.0, ge=0, le=1)


def _record_surveillance_event(event: SurveillanceEvent) -> list[dict]:
    """Records a camera sighting and evaluates two alerts: the suspect being on
    an FIR watchlist, and the suspect appearing in a different zone earlier."""
    query = """
    MERGE (c:Camera {id: $camera_id})
      ON CREATE SET c.zone = $zone
      ON MATCH SET c.zone = $zone
    MERGE (s:Suspect {id: $suspect_id})
    CREATE (s)-[obs:SEEN_AT {
        timestamp: $timestamp,
        source_id: 'SURV-' + $camera_id,
        confidence: $confidence,
        evidence: 'Synthetic camera match at ' + $zone
    }]->(c)
    WITH s, c, obs
    OPTIONAL MATCH (s)-[:LINKED_TO_FIR]->(f:FIR)
    OPTIONAL MATCH (s)-[prev:SEEN_AT]->(prev_c:Camera)
      WHERE prev_c.id <> c.id AND prev_c.zone <> c.zone AND prev.timestamp < obs.timestamp
    RETURN s.id AS suspect, c.zone AS current_zone, obs.timestamp AS current_time,
           collect(DISTINCT f.id) AS firs,
           collect(DISTINCT {zone: prev_c.zone, at: prev.timestamp}) AS previous
    """
    with _neo4j.session() as session:
        record = session.run(
            query,
            camera_id=event.camera_id,
            zone=event.zone,
            suspect_id=event.suspect_name,
            timestamp=event.timestamp,
            confidence=event.match_confidence,
        ).single()

    if record is None:
        return []

    alerts: list[dict] = []
    firs = [f for f in record["firs"] if f]
    if firs:
        alerts.append(
            {
                "type": "WATCHLIST_MATCH",
                "suspect": record["suspect"],
                "explanation": (
                    f"{record['suspect']} was seen in {record['current_zone']} and is named in "
                    f"{len(firs)} active FIR(s)."
                ),
                "sources": [*firs[:3], f"SURV-{event.camera_id}"],
            }
        )

    prior = [p for p in record["previous"] if p and p.get("zone")]
    if prior:
        latest = max(prior, key=lambda p: p["at"] or "")
        alerts.append(
            {
                "type": "CROSS_ZONE_MOVEMENT",
                "suspect": record["suspect"],
                "explanation": (
                    f"{record['suspect']} moved from {latest['zone']} ({latest['at']}) to "
                    f"{record['current_zone']} ({record['current_time']})."
                ),
                "sources": [f"SURV-{event.camera_id}"],
            }
        )
    return alerts


@router.post("/surveillance/event")
async def surveillance_event(
    event: SurveillanceEvent, current_user: dict = Depends(get_current_user)
):
    alerts = _record_surveillance_event(event)
    log_action(
        current_user,
        action="record_surveillance_event",
        resource=f"camera:{event.camera_id}",
    )
    return {"status": "recorded", "suspect": event.suspect_name, "alerts": alerts}


@router.get("/surveillance/sightings")
async def surveillance_sightings(current_user: dict = Depends(get_current_user)):
    query = """
    MATCH (s:Suspect)-[o:SEEN_AT]->(c:Camera)
    RETURN s.id AS suspect, c.id AS camera, c.zone AS zone,
           o.timestamp AS timestamp, o.confidence AS confidence
    ORDER BY o.timestamp DESC
    LIMIT 200
    """
    with _neo4j.session() as session:
        rows = [dict(r) for r in session.run(query)]
    log_action(current_user, action="view_sightings", resource="surveillance")
    return rows


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

BURNER_PERCENTILE = float(os.getenv("BURNER_PERCENTILE", "0.95"))
BURNER_MIN_CALLS = int(os.getenv("BURNER_MIN_CALLS", "5"))


def burner_cutoff(call_counts: list[int]) -> int:
    """Adaptive burner threshold: the top (1 - BURNER_PERCENTILE) of callers by outgoing volume,
    never below BURNER_MIN_CALLS. A fixed "2+ calls" rule flags every phone in a real CDR set."""
    if not call_counts:
        return BURNER_MIN_CALLS
    ordered = sorted(call_counts)
    index = min(len(ordered) - 1, int(BURNER_PERCENTILE * len(ordered)))
    return max(BURNER_MIN_CALLS, ordered[index])


_PATTERNS_TTL = float(os.getenv("PATTERNS_CACHE_SECONDS", "60"))
_patterns_cache: dict = {"at": 0.0, "rows": []}


def _pattern_feedback_map() -> dict:
    with get_cursor() as cur:
        cur.execute("SELECT pattern_id, verdict FROM pattern_feedback")
        return {r["pattern_id"]: r["verdict"] for r in cur.fetchall()}


def _verdict_to_status(verdict: Optional[str]) -> str:
    return {"useful": "confirmed", "false_positive": "dismissed", "escalated": "escalated"}.get(verdict, "new")


@router.get("/patterns")
async def list_patterns(current_user: dict = Depends(get_current_user)):
    feedback = _pattern_feedback_map()
    if time.monotonic() - _patterns_cache["at"] < _PATTERNS_TTL and _patterns_cache["rows"]:
        # Graph scan is cached briefly; analyst verdicts are always applied fresh.
        fresh = [{**p, "status": _verdict_to_status(feedback.get(p["pattern_id"]))} for p in _patterns_cache["rows"]]
        log_action(current_user, action="list_patterns", resource="patterns")
        return fresh
    now = datetime.now(timezone.utc).isoformat()
    patterns: list[dict] = []

    with _neo4j.session() as session:
        call_rows = [
            (row["phone"], row["call_count"])
            for row in session.run(
                """
                MATCH (p:Phone)-[c:CALLED]->(:Phone)
                WITH p, count(c) AS call_count
                RETURN p.id AS phone, call_count
                ORDER BY call_count DESC
                """
            )
        ]
        cutoff = burner_cutoff([n for _, n in call_rows])
        top_calls = call_rows[0][1] if call_rows else 1
        for phone, call_count in call_rows:
            if call_count < cutoff:
                break
            pattern_id = f"burner-{phone}"
            confidence = round(min(0.6 + 0.37 * call_count / top_calls, 0.97), 2)
            patterns.append(
                {
                    "pattern_id": pattern_id,
                    "pattern_type": "burner_phone_cluster",
                    "confidence": confidence,
                    "description": f"Phone {phone} placed {call_count} calls, far above typical volume.",
                    "explanation": f"{call_count} outgoing calls from {phone} put it in the top {round((1 - BURNER_PERCENTILE) * 100)}% of callers (threshold: {cutoff}+ calls out of {len(call_rows)} phones), matching a burner-phone usage signature.",
                    "entities": [phone],
                    "detected_at": now,
                    "status": _verdict_to_status(feedback.get(pattern_id)),
                    "source": "burner_heuristic",
                }
            )
        financial_result = session.run(
            """
            MATCH (source:FinancialAccount)-[t:TRANSACTED_WITH]->(target:FinancialAccount)
            WHERE t.amount >= 9000 AND t.amount < 10000
            WITH target, count(t) AS transfer_count, sum(t.amount) AS total_amount,
                 collect(source.id)[..10] AS sources
            WHERE transfer_count >= 3
            RETURN target.id AS account, transfer_count, total_amount, sources
            ORDER BY transfer_count DESC
            """
        )
        for row in financial_result:
            pattern_id = f"financial-structuring-{row['account']}"
            patterns.append(
                {
                    "pattern_id": pattern_id,
                    "pattern_type": "financial_structuring",
                    "confidence": round(min(0.95, 0.55 + row["transfer_count"] * 0.05), 2),
                    "description": f"Account {row['account']} received {row['transfer_count']} transfers just below the reporting threshold.",
                    "explanation": f"{row['transfer_count']} transfers totaling {row['total_amount']} were received from separate accounts in the 9,000–10,000 range.",
                    "entities": [row["account"], *row["sources"]],
                    "detected_at": now,
                    "status": _verdict_to_status(feedback.get(pattern_id)),
                    "source": "financial_cluster",
                }
            )

    centrality_rows = await run_in_threadpool(_compute_centrality)
    scores = sorted(r["pagerank"] for r in centrality_rows)
    median_pr = scores[len(scores) // 2] if scores else 0.0
    max_pr = scores[-1] if scores else 0.0
    # A hub is a node several times more central than the typical node; confidence is relative to the top hub.
    hubs = [r for r in centrality_rows if r["pagerank"] >= max(3 * median_pr, 1e-9)][:5]
    for row in hubs:
        pattern_id = f"centrality-{row['entity_value']}"
        confidence = round(min(0.95, 0.55 + 0.4 * (row["pagerank"] / max_pr if max_pr else 0)), 2)
        patterns.append(
            {
                "pattern_id": pattern_id,
                "pattern_type": "central_network_hub",
                "confidence": confidence,
                "description": f"{row['entity_value']} is a high-centrality hub in the network graph (PageRank {row['pagerank']}).",
                "explanation": f"PageRank {row['pagerank']} (typical node: {round(median_pr, 4)}) and betweenness {row['betweenness']} place {row['entity_value']} among the most structurally important nodes, suggesting a coordination or hub role.",
                "entities": [row["entity_value"]],
                "detected_at": now,
                "status": _verdict_to_status(feedback.get(pattern_id)),
                "source": "centrality",
            }
        )

    _patterns_cache.update(at=time.monotonic(), rows=patterns)
    log_action(current_user, action="list_patterns", resource="patterns")
    return patterns


class PatternFeedbackRequest(BaseModel):
    verdict: Literal["useful", "false_positive", "escalated"]


@router.post("/patterns/{pattern_id}/feedback")
async def pattern_feedback(pattern_id: str, payload: PatternFeedbackRequest, current_user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO pattern_feedback (pattern_id, verdict, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (pattern_id) DO UPDATE SET verdict = EXCLUDED.verdict, updated_at = now()
            """,
            (pattern_id, payload.verdict),
        )
    log_action(current_user, action="pattern_feedback", resource=f"pattern:{pattern_id}")
    all_patterns = await list_patterns(current_user)
    match = next((p for p in all_patterns if p["pattern_id"] == pattern_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Pattern not found.")
    return match


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertRuleRequest(BaseModel):
    entity_value: str


@router.post("/alerts/rules")
async def create_alert_rule(payload: AlertRuleRequest, current_user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO alert_rules (entity_value, created_by) VALUES (%s, %s) RETURNING *",
            (payload.entity_value, current_user["full_name"]),
        )
        row = cur.fetchone()
    log_action(current_user, action="create_alert_rule", resource=f"entity:{payload.entity_value}")
    return {"rule_id": str(row["rule_id"]), "entity_value": row["entity_value"], "created_at": row["created_at"].isoformat()}


@router.get("/alerts/rules")
async def list_alert_rules(current_user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM alert_rules ORDER BY created_at DESC")
        rows = cur.fetchall()
    return [{"rule_id": str(r["rule_id"]), "entity_value": r["entity_value"], "created_at": r["created_at"].isoformat()} for r in rows]


@router.get("/alerts")
async def list_alerts(current_user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM alerts ORDER BY triggered_at DESC LIMIT 200")
        rows = cur.fetchall()
    log_action(current_user, action="list_alerts", resource="alerts")
    return [
        {
            "alert_id": str(r["alert_id"]),
            "rule_id": str(r["rule_id"]) if r["rule_id"] else "",
            "entity_value": r["entity_value"],
            "message": r["message"],
            "triggered_at": r["triggered_at"].isoformat(),
            "read_status": r["read_status"],
        }
        for r in rows
    ]


@router.post("/alerts/read-all")
async def mark_all_alerts_read(current_user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE alerts SET read_status = true WHERE read_status = false")
        updated = cur.rowcount
    log_action(current_user, action="alerts_mark_all_read", resource="alerts")
    return {"updated": updated}


@router.post("/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: str, current_user: dict = Depends(get_current_user)):
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE alerts SET read_status = true WHERE alert_id = %s RETURNING alert_id", (alert_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Alert not found.")
    return {"alert_id": alert_id, "read_status": True}


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

_summary_cache: dict = {"at": 0.0, "data": None}
_SUMMARY_TTL = float(os.getenv("SUMMARY_CACHE_SECONDS", "60"))


def _compute_summary() -> dict:
    with _neo4j.session() as session:
        labels = {r["l"]: r["c"] for r in session.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c")}
        rels = {r["t"]: r["c"] for r in session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
    return {
        "firs": labels.get("FIR", 0),
        "suspects": labels.get("Suspect", 0),
        "phones": labels.get("Phone", 0),
        "accounts": labels.get("FinancialAccount", 0),
        "calls": rels.get("CALLED", 0),
        "transactions": rels.get("TRANSACTED_WITH", 0),
        "sightings": sum(c for t, c in rels.items() if "SIGHT" in t),
    }


@router.get("/dashboard/summary")
async def dashboard_summary(current_user: dict = Depends(get_current_user)):
    """Real dataset counts for the dashboard (cached briefly; the graph scan is not free)."""
    if _summary_cache["data"] is None or time.monotonic() - _summary_cache["at"] > _SUMMARY_TTL:
        _summary_cache.update(at=time.monotonic(), data=await run_in_threadpool(_compute_summary))
    return _summary_cache["data"]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

class ReportExportRequest(BaseModel):
    case_id: str
    sections: list[str] = Field(default_factory=list)
    justification: Optional[str] = None


def _pdf_safe(text: str) -> str:
    """The core PDF fonts are latin-1 only. Fold common typographic characters
    and replace anything else outside the range, so a stray character in a
    user-entered case title or note cannot fail the whole export."""
    folded = (
        str(text)
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2026", "...")
    )
    return folded.encode("latin-1", "replace").decode("latin-1")


@router.post("/reports/export")
async def export_report(payload: ReportExportRequest, current_user: dict = Depends(get_current_user)):
    case = _fetch_case_detail(payload.case_id)
    _assert_case_scope(case, current_user)
    if case["is_sensitive"] and not payload.justification:
        raise HTTPException(
            status_code=428,
            detail="This case is marked sensitive. Provide a justification to export it.",
        )

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, _pdf_safe(case["title"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 8, _pdf_safe(f"{case['fir_number']}  |  {case['jurisdiction']}  |  Status: {case['status']}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    if "Entity List" in payload.sections:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 8, _pdf_safe("Entity List"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        if case["entities"]:
            for e in case["entities"]:
                pdf.multi_cell(0, 6, _pdf_safe(f"- {e['entity_value']} ({e['entity_type']}) — pinned by {e['linked_by']}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.multi_cell(0, 6, _pdf_safe("No entities pinned."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    if "Notes" in payload.sections:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 8, _pdf_safe("Notes"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        if case["notes"]:
            for n in case["notes"]:
                pdf.multi_cell(0, 6, _pdf_safe(f"- {n['content']} (by {n['author']})"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.multi_cell(0, 6, _pdf_safe("No notes."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    if "Pattern Findings" in payload.sections:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 8, _pdf_safe("Pattern Findings"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        patterns = await list_patterns(current_user)
        if patterns:
            for p in patterns:
                pdf.multi_cell(0, 6, _pdf_safe(f"- {p['description']} ({round(p['confidence'] * 100)}% confidence)"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.multi_cell(0, 6, _pdf_safe("No pattern findings."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    if "Source Citations" in payload.sections:
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 8, _pdf_safe("Source Citations"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 10)
        if case["entities"]:
            for e in case["entities"]:
                pdf.multi_cell(0, 6, _pdf_safe(f"- {case['fir_number']}: {e['entity_value']}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.multi_cell(0, 6, _pdf_safe("No citations."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "I", 8)
    pdf.ln(6)
    pdf.multi_cell(0, 5, _pdf_safe("Generated by ARGUS. AI-derived content must be corroborated before evidentiary use. Export logged to audit trail."), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf_bytes = bytes(pdf.output())
    log_action(
        current_user,
        action="export_report",
        resource=f"case:{payload.case_id}:{case['fir_number']}-report.pdf",
        justification=payload.justification,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{case["fir_number"]}-report.pdf"'},
    )


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@router.get("/audit")
async def get_audit_log(
    user: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_role("admin")),
):
    with get_cursor() as cur:
        filters = []
        params: list[str] = []
        if user:
            filters.append("user_full_name ILIKE %s")
            params.append(f"%{user}%")
        if action:
            filters.append("action = %s")
            params.append(action)
        if start:
            filters.append("occurred_at >= %s")
            params.append(start)
        if end:
            filters.append("occurred_at <= %s")
            params.append(end)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        cur.execute(
            f"SELECT * FROM audit_log {where} ORDER BY occurred_at DESC LIMIT 500",
            params,
        )
        rows = cur.fetchall()
    return [
        {
            "audit_id": str(r["audit_id"]),
            "user": r["user_full_name"],
            "role": r["role"],
            "action": r["action"],
            "resource": r["resource"],
            "justification": r["justification"],
            "occurred_at": r["occurred_at"].isoformat(),
            "record_hash": r["record_hash"],
            "previous_hash": r["previous_hash"],
        }
        for r in rows
    ]


@router.get("/audit/verify")
async def verify_audit_chain(current_user: dict = Depends(require_role("admin"))):
    result = verify_chain()
    log_action(current_user, action="verify_audit_chain", resource="audit")
    return result


@router.get("/admin/ingestion-health")
async def ingestion_health(current_user: dict = Depends(require_role("admin", "supervisor"))):
    counts = {"queued": 0, "processing": 0, "processed": 0, "quarantined": 0}
    last_run = None
    for key in _redis.scan_iter("argus:jobs:*"):
        raw = _redis.get(key)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            status = payload.get("status", "")
            if payload.get("updated_at") and (last_run is None or payload["updated_at"] > last_run):
                last_run = payload["updated_at"]
        except Exception:
            continue
        if status in ("pending", "queued", "queued_for_graph"):
            counts["queued"] += 1
        elif status == "processing":
            counts["processing"] += 1
        elif status == "processed":
            counts["processed"] += 1
        elif status in ("failed", "quarantined"):
            counts["quarantined"] += 1
    return {
        **counts,
        "status": "healthy" if counts["processing"] == 0 and counts["quarantined"] == 0 else "degraded",
        "lastRun": last_run,
    }
