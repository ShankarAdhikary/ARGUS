"""Tamper-evident evidence ledger.

Every file stored in MinIO by an ingest endpoint gets a ledger row holding the file's SHA-256, who uploaded it, when,
and a hash chained to the previous row. Editing a row breaks its own hash and every later link; replacing a file in
storage makes its current SHA-256 differ from the ledger. `verify_file` and `verify_chain` detect both.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from db import get_cursor

LEDGER_LOCK = 724004


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="microseconds")


def compute_row_hash(*, file_id: str, file_sha256: str, case_id: Optional[str], uploaded_by: str, uploaded_at: datetime, prev_hash: Optional[str]) -> str:
    payload = {
        "file_id": file_id, "file_sha256": file_sha256, "case_id": str(case_id) if case_id else "",
        "uploaded_by": uploaded_by, "uploaded_at": _iso(uploaded_at), "prev_hash": prev_hash or "",
    }
    return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def record_upload(file_id: str, data: bytes, uploaded_by: str, case_id: Optional[str] = None,
                  file_name: Optional[str] = None, content_type: Optional[str] = None) -> dict:
    """Append a ledger row for `data` stored at `file_id`. Raises on failure so the caller can refuse the upload."""
    file_sha256 = sha256_hex(data)
    uploaded_at = datetime.now(timezone.utc)
    with get_cursor(commit=True) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LEDGER_LOCK,))  # keeps the chain linear across workers
        cur.execute("SELECT row_hash FROM evidence_ledger ORDER BY seq DESC LIMIT 1")
        last = cur.fetchone()
        prev_hash = last["row_hash"] if last else None
        row_hash = compute_row_hash(file_id=file_id, file_sha256=file_sha256, case_id=case_id, uploaded_by=uploaded_by,
                                    uploaded_at=uploaded_at, prev_hash=prev_hash)
        cur.execute(
            """
            INSERT INTO evidence_ledger (file_id, file_sha256, case_id, uploaded_by, uploaded_at, prev_hash, row_hash, file_name, size_bytes, content_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING ledger_id, seq
            """,
            (file_id, file_sha256, case_id, uploaded_by, uploaded_at, prev_hash, row_hash, file_name, len(data), content_type),
        )
        inserted = cur.fetchone()
    return {"ledger_id": str(inserted["ledger_id"]), "file_id": file_id, "file_sha256": file_sha256, "row_hash": row_hash}


def check_chain(rows: list[dict]) -> dict:
    """Verify an ordered list of ledger rows (oldest first). Marks each row `row_ok` and reports the first break."""
    expected_prev: Optional[str] = None
    first_break = None
    for row in rows:
        recomputed = compute_row_hash(file_id=row["file_id"], file_sha256=row["file_sha256"], case_id=row["case_id"],
                                      uploaded_by=row["uploaded_by"], uploaded_at=row["uploaded_at"], prev_hash=row["prev_hash"])
        link_ok = (row["prev_hash"] or None) == expected_prev
        hash_ok = recomputed == row["row_hash"]
        row["row_ok"] = link_ok and hash_ok
        if not row["row_ok"] and first_break is None:
            first_break = {"ledger_id": str(row["ledger_id"]), "reason": "row hash does not match its contents" if not hash_ok else "link to previous row is broken"}
        expected_prev = row["row_hash"]
    return {"valid": first_break is None, "checked": len(rows), "first_break": first_break}


def load_chain() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM evidence_ledger ORDER BY seq ASC")
        return cur.fetchall()


def verify_delta(stored: str, computed: Optional[str]) -> str:
    if computed is None:
        return "File is missing from storage."
    if stored == computed:
        return "File matches the hash recorded at upload."
    return "File content has changed since upload: the current SHA-256 differs from the ledger."
