"""Evidence ledger endpoints (phase 5)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

import evidence
import platform_api
from auth_security import get_current_user
from config import BUCKET_NAME
from db import get_cursor

logger = logging.getLogger("argus.evidence")
router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])

CROSS_ROLES = ("admin", "supervisor", "analyst")


def _fail(exc: Exception) -> HTTPException:
    logger.exception("evidence endpoint failure: %s", exc)
    return HTTPException(status_code=503, detail="An internal error occurred. Contact your system administrator.")


def _authorize_case(case_id: str, justification: Optional[str], user: dict) -> None:
    detail = platform_api._fetch_case_detail(case_id)
    platform_api._assert_case_scope(detail, user)
    if detail["is_sensitive"] and not justification:
        platform_api.log_action(user, action="denied_sensitive_case_access", resource=f"case:{case_id}:evidence")
        raise HTTPException(status_code=428, detail="This case is marked sensitive. Provide ?justification= to view its evidence.")


def _public(row: dict) -> dict:
    return {
        "ledger_id": str(row["ledger_id"]), "file_id": row["file_id"], "file_name": row.get("file_name"),
        "file_sha256": row["file_sha256"], "case_id": str(row["case_id"]) if row["case_id"] else None,
        "uploaded_by": row["uploaded_by"], "uploaded_at": row["uploaded_at"].isoformat(),
        "size_bytes": row.get("size_bytes"), "prev_hash": row["prev_hash"], "row_hash": row["row_hash"],
        "row_ok": row.get("row_ok"),
    }


def _read_minio(file_id: str) -> Optional[bytes]:
    from minio.error import S3Error

    client = platform_api_minio()
    try:
        response = client.get_object(BUCKET_NAME, file_id)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def platform_api_minio():
    import main  # the API's shared MinIO client

    return main.minio_client


@router.get("/ledger")
async def get_ledger(case_id: str, justification: Optional[str] = Query(default=None), current_user: dict = Depends(get_current_user)):
    """Full ledger for a case, each row marked intact/tampered by re-deriving its hash from the whole chain."""
    try:
        await run_in_threadpool(_authorize_case, case_id, justification, current_user)
        chain = await run_in_threadpool(evidence.load_chain)
    except HTTPException:
        raise
    except Exception as exc:
        raise _fail(exc) from exc
    status = evidence.check_chain(chain)
    rows = [_public(r) for r in chain if r["case_id"] and str(r["case_id"]) == case_id]
    platform_api.log_action(current_user, action="view_evidence_ledger", resource=f"case:{case_id}:evidence",
                            justification=justification, extra={"rows": len(rows), "chain_valid": status["valid"]})
    return {"status": "success", "case_id": case_id, "entries": rows, "chain": status}


@router.get("/verify/{file_id:path}")
async def verify_file(file_id: str, justification: Optional[str] = Query(default=None), current_user: dict = Depends(get_current_user)):
    """Re-hash the file currently in MinIO and compare it with the ledger entry recorded at upload."""
    def load() -> Optional[dict]:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM evidence_ledger WHERE file_id = %s ORDER BY seq DESC LIMIT 1", (file_id,))
            return cur.fetchone()

    try:
        entry = await run_in_threadpool(load)
        if not entry:
            raise HTTPException(status_code=404, detail="No ledger entry for that file.")
        if entry["case_id"]:
            await run_in_threadpool(_authorize_case, str(entry["case_id"]), justification, current_user)
        elif current_user["role"] not in CROSS_ROLES and entry["uploaded_by"] != current_user["full_name"]:
            raise HTTPException(status_code=403, detail="Not permitted to verify this file.")
        data = await run_in_threadpool(_read_minio, file_id)
        chain = evidence.check_chain(await run_in_threadpool(evidence.load_chain))
    except HTTPException:
        raise
    except Exception as exc:
        raise _fail(exc) from exc
    computed = evidence.sha256_hex(data) if data is not None else None
    intact = computed == entry["file_sha256"] and chain["valid"]
    platform_api.log_action(current_user, action="verify_evidence", resource=f"file:{file_id[:150]}",
                            extra={"intact": intact, "file_matches": computed == entry["file_sha256"], "chain_valid": chain["valid"]})
    return {
        "intact": intact,
        "file_id": file_id,
        "stored_hash": entry["file_sha256"],
        "computed_hash": computed,
        "delta_message": evidence.verify_delta(entry["file_sha256"], computed)
        + ("" if chain["valid"] else " The ledger chain itself has been altered."),
        "ledger_id": str(entry["ledger_id"]),
        "chain_valid": chain["valid"],
    }
