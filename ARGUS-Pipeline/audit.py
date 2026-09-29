"""Single reusable tamper-evident audit-log writer, called from every read/write endpoint
on the platform track (auth, cases, patterns, resolve, alerts, reports,
admin)."""

from datetime import datetime
import hashlib
import json
from typing import Optional

from db import get_cursor


def log_action(user: dict, action: str, resource: str, justification: Optional[str] = None, extra: Optional[dict] = None) -> None:
    with get_cursor(commit=True) as cur:
        # Serialize writers so the hash chain stays linear; released on commit.
        cur.execute("SELECT pg_advisory_xact_lock(724001)")
        cur.execute(
            """
            SELECT record_hash
            FROM audit_log
            WHERE record_hash IS NOT NULL
            ORDER BY occurred_at DESC, audit_id DESC
            LIMIT 1
            """,
        )
        previous_hash_row = cur.fetchone()
        previous_hash = previous_hash_row["record_hash"] if previous_hash_row else None
        cur.execute(
            """
            INSERT INTO audit_log
                (user_full_name, role, action, resource, justification, previous_hash, occurred_at, details)
            VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp(), %s::jsonb)
            RETURNING audit_id, occurred_at
            """,
            (user["full_name"], user["role"], action, resource, justification, previous_hash,
             json.dumps(extra, sort_keys=True) if extra is not None else None),
        )
        inserted = cur.fetchone()
        payload = {
            "audit_id": str(inserted["audit_id"]),
            "user_full_name": user["full_name"],
            "role": user["role"],
            "action": action,
            "resource": resource,
            "justification": justification,
            "occurred_at": inserted["occurred_at"].isoformat(),
            "previous_hash": previous_hash,
        }
        if extra is not None:  # older rows (no details) keep their original hash
            payload["details"] = extra
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        record_hash = hashlib.sha256(canonical).hexdigest()
        cur.execute(
            "UPDATE audit_log SET record_hash = %s WHERE audit_id = %s",
            (record_hash, inserted["audit_id"]),
        )


def verify_chain() -> dict:
    """Verify hash links and payload hashes for the tamper-evident audit chain."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT audit_id, user_full_name, role, action, resource, justification,
                   occurred_at, previous_hash, record_hash, details
            FROM audit_log
            WHERE record_hash IS NOT NULL
            ORDER BY occurred_at ASC, audit_id ASC
            """
        )
        rows = cur.fetchall()
    expected_previous = None
    checked = 0
    for row in rows:
        if row["previous_hash"] != expected_previous:
            return {"valid": False, "checked": checked, "error": "Audit chain link mismatch.", "audit_id": str(row["audit_id"])}
        payload = {
            "audit_id": str(row["audit_id"]),
            "user_full_name": row["user_full_name"],
            "role": row["role"],
            "action": row["action"],
            "resource": row["resource"],
            "justification": row["justification"],
            "occurred_at": row["occurred_at"].isoformat(),
            "previous_hash": row["previous_hash"],
        }
        if row["details"] is not None:
            payload["details"] = row["details"]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        actual = hashlib.sha256(canonical).hexdigest()
        if actual != row["record_hash"]:
            return {"valid": False, "checked": checked, "error": "Audit record hash mismatch.", "audit_id": str(row["audit_id"])}
        expected_previous = actual
        checked += 1
    return {"valid": True, "checked": checked, "legacy_unhashed": _legacy_count(), "error": None}


def _legacy_count() -> int:
    with get_cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM audit_log WHERE record_hash IS NULL")
        return int(cur.fetchone()["count"])
