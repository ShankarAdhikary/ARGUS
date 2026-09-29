from io import BytesIO
import asyncio
import hashlib
import logging
import uuid as _uuid_module
import redis
from elasticsearch import Elasticsearch
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from minio import Minio
from neo4j import GraphDatabase
import json
import os
import time
import uuid
import cv2
import numpy as np
import faiss
from pathlib import Path
from deepface import DeepFace
import zipfile
import shutil
import re
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import (
    BUCKET_NAME,
    CORS_ORIGINS,
    MAX_REQUEST_BYTES,
    assert_production_safe,
    ELASTICSEARCH_URL,
    ES_AUTH_KWARGS,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
)
from text_extraction import extract_candidates
from db import init_db
from face_index import FaceIndex
from jurisdictions import UNASSIGNED, jurisdiction_for_station
from platform_api import router as platform_router, check_and_fire_alerts, log_action, burner_cutoff, search_scope
from auth_security import get_current_user, require_role
from platform_api import enforce_case_access

logger = logging.getLogger("argus.api")

def _safe_detail(exc: Exception) -> str:
    """Return a generic error message — never expose internal exception details."""
    logger.exception("Internal error: %s", exc)
    return "An internal error occurred. Contact your system administrator."


app = FastAPI(
    title="ARGUS Ingestion Pipeline",
    version="1.0.0",
    description="Frozen ARGUS hackathon MVP API contract. Update openapi-mvp-v1.json before changing endpoints.",
)
app.include_router(platform_router)


@app.on_event("startup")
async def _startup() -> None:
    """Creates the Postgres schema and seeds demo users if they don't exist."""
    assert_production_safe()
    # Load the face models in the background so the first Hunt/Enroll isn't a ~minute-long cold start.
    asyncio.create_task(asyncio.to_thread(_warm_face_models))
    try:
        init_db()
    except Exception as exc:  # pragma: no cover - surfaced via /health instead
        print(f"[!] Postgres init failed (platform endpoints will error until fixed): {exc}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def _security_middleware(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request body too large."})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response

os.makedirs("uploads", exist_ok=True)
# NOTE: uploads are intentionally NOT mounted as static files; suspect photos are
# biometric data and are only served through the authenticated endpoint below.
_IMAGE_NAME_RE = re.compile(r"^[0-9a-fA-F-]{36}\.[A-Za-z0-9]{1,5}$")


@app.get("/api/v1/biometric/image/{filename}")
async def get_biometric_image(
    filename: str,
    current_user: dict = Depends(require_role("admin", "investigator", "supervisor", "analyst")),
):
    if not _IMAGE_NAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="Image not found.")
    path = Path("uploads") / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    log_action(current_user, action="view_biometric_image", resource=f"image:{filename}")
    return FileResponse(path)

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, password=REDIS_PASSWORD)

es_client = Elasticsearch(
    ELASTICSEARCH_URL,
    headers={"Accept": "application/vnd.elasticsearch+json; compatible-with=8"},
    **ES_AUTH_KWARGS,
)

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

try:
    if not minio_client.bucket_exists(BUCKET_NAME):
        minio_client.make_bucket(BUCKET_NAME)
except Exception:
    pass

embedding_dimension = 512
face_index = FaceIndex(embedding_dimension, os.getenv("FACE_INDEX_DIR", "uploads/face_index"))


def _warm_face_models() -> None:
    try:
        _represent(np.zeros((320, 320, 3), dtype=np.uint8))
    except Exception as exc:  # pragma: no cover - warm-up is best effort
        print(f"[!] Face model warm-up failed (first request will load them): {exc}")


def _represent(img):
    """Blocking ArcFace embedding; always call via asyncio.to_thread so the event loop stays free."""
    return DeepFace.represent(img_path=img, model_name="ArcFace", detector_backend="retinaface", enforce_detection=False)


class TextIngestionRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=10, max_length=50_000)
    source_type: str = Field(default="fir", pattern="^(fir|surveillance_report|intel_note)$")


def _put_object(object_key: str, payload: bytes, content_type: str) -> None:
    """Store source material in MinIO, creating the MVP bucket when needed."""
    if not minio_client.bucket_exists(BUCKET_NAME):
        minio_client.make_bucket(BUCKET_NAME)
    minio_client.put_object(
        BUCKET_NAME,
        object_key,
        BytesIO(payload),
        len(payload),
        content_type=content_type,
    )


def _queue_job(event: dict) -> None:
    redis_client.set(
        f"argus:jobs:{event['job_id']}",
        json.dumps({"status": "pending", "updated_at": _timestamp(), **event}),
    )
    redis_client.rpush("argus_ingest_queue", json.dumps(event))


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@app.get("/health")
async def health_check():
    """Reports which local MVP dependencies are reachable."""
    checks = {}
    def _check_postgres() -> bool:
        from db import get_cursor

        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return True

    for name, check in {
        "redis": lambda: redis_client.ping(),
        "elasticsearch": lambda: es_client.ping(),
        "minio": lambda: minio_client.bucket_exists(BUCKET_NAME),
        "postgres": _check_postgres,
    }.items():
        try:
            checks[name] = bool(check())
        except Exception:
            checks[name] = False
    try:
        neo4j_driver.verify_connectivity()
        checks["neo4j"] = True
    except Exception:
        checks["neo4j"] = False
    return {"status": "healthy" if all(checks.values()) else "degraded", "dependencies": checks}


@app.post("/api/v1/ingest")
async def ingest_dataset(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("admin", "investigator", "supervisor")),
):
    try:
        file_bytes = await file.read()
        filename = file.filename or "dataset.json"
        lowered_filename = filename.lower()
        dataset_type = (
            "fir" if "fir" in lowered_filename
            else "cdr" if "cdr" in lowered_filename
            else "financial" if "financial" in lowered_filename or "transaction" in lowered_filename
            else "surveillance" if "surveillance" in lowered_filename
            else "unknown"
        )

        checksum = hashlib.sha256(file_bytes).hexdigest()
        job_id = str(uuid.uuid4())
        checksum_key = f"argus:ingest:checksum:{checksum}"
        if not redis_client.set(checksum_key, job_id, nx=True):
            existing_job = redis_client.get(checksum_key).decode("utf-8")
            return {
                "status": "duplicate",
                "job_id": existing_job,
                "filename": filename,
                "message": "This exact payload has already been queued.",
            }

        file_path = f"raw/{checksum[:12]}-{filename}"
        try:
            _put_object(file_path, file_bytes, file.content_type or "application/json")
            record_count = None
            try:
                parsed = json.loads(file_bytes)
                if isinstance(parsed, list):
                    record_count = len(parsed)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            _queue_job({
                "job_id": job_id,
                "dataset_type": dataset_type,
                "filename": filename,
                "storage_path": file_path,
                "sha256": checksum,
                "record_count": record_count,
            })
        except Exception:
            redis_client.delete(checksum_key)
            raise
        
        return {
            "status": "queued",
            "job_id": job_id,
            "filename": filename,
            "total_records_queued": record_count,
            "message": "File stored and queued for asynchronous processing."
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_safe_detail(e)) from e


@app.get("/api/v1/jobs/{job_id}")
async def get_job_status(job_id: str, current_user: dict = Depends(get_current_user)):
    status = redis_client.get(f"argus:jobs:{job_id}")
    if not status:
        raise HTTPException(status_code=404, detail="Job not found.")
    return json.loads(status)


@app.post("/api/v1/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    dataset_type: str | None = Query(default=None, pattern="^(fir|cdr)$"),
    current_user: dict = Depends(require_role("admin", "supervisor")),
):
    raw_status = redis_client.get(f"argus:jobs:{job_id}")
    if not raw_status:
        raise HTTPException(status_code=404, detail="Job not found.")
    job = json.loads(raw_status)
    if job.get("status") not in {"quarantined", "failed"}:
        raise HTTPException(status_code=409, detail="Only quarantined or failed jobs can be retried.")
    event = {
        key: job[key]
        for key in ("job_id", "dataset_type", "filename", "storage_path", "sha256", "record_count")
        if key in job
    }
    if dataset_type:
        event["dataset_type"] = dataset_type
    event["retry_requested_by"] = current_user["employee_id"]
    redis_client.set(
        f"argus:jobs:{job_id}",
        json.dumps({"status": "pending", "updated_at": _timestamp(), **event}),
    )
    redis_client.rpush("argus_ingest_queue", json.dumps(event))
    return {"status": "pending", "job_id": job_id, "message": "Job requeued for processing."}


@app.post("/api/v1/ingest/text")
async def ingest_text(
    payload: TextIngestionRequest,
    current_user: dict = Depends(require_role("admin", "investigator", "supervisor")),
):
    """Extract validated candidate entities from unstructured text and queue them for graph ingestion."""
    try:
        extraction = await asyncio.to_thread(extract_candidates, payload.text)
        normalized = {
            "source_id": payload.source_id,
            "source_type": payload.source_type,
            "raw_text": payload.text,
            **extraction.model_dump(),
        }
        encoded = json.dumps(normalized).encode("utf-8")
        checksum = hashlib.sha256(encoded).hexdigest()
        job_id = str(uuid.uuid4())
        checksum_key = f"argus:extraction:checksum:{checksum}"
        if not redis_client.set(checksum_key, job_id, nx=True):
            existing_job = redis_client.get(checksum_key).decode("utf-8")
            return {"status": "duplicate", "job_id": existing_job, "source_id": payload.source_id}

        storage_path = f"normalized/{payload.source_id}-{checksum[:12]}.json"
        try:
            _put_object(storage_path, encoded, "application/json")
            event = {
                "job_id": job_id,
                "dataset_type": "normalized_extraction",
                "source_id": payload.source_id,
                "storage_path": storage_path,
                "sha256": checksum,
                "entity_count": len(extraction.entities),
                "relationship_count": len(extraction.relationships),
            }
            # Member 2 consumes only validated normalized candidates from this queue.
            redis_client.set(f"argus:jobs:{job_id}", json.dumps({"status": "queued_for_graph", **event}))
            redis_client.rpush("argus_graph_queue", json.dumps(event))
        except Exception:
            redis_client.delete(checksum_key)
            raise

        try:
            check_and_fire_alerts([entity.value for entity in extraction.entities])
        except Exception as exc:
            print(f"[!] Alert check failed (non-fatal): {exc}")

        return {
            "status": "queued_for_graph",
            "job_id": job_id,
            "source_id": payload.source_id,
            "extraction_method": extraction.extraction_method,
            "entities": extraction.entities,
            "relationships": extraction.relationships,
            "message": "Validated candidate extraction queued for graph ingestion. Verify candidates before operational use.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_safe_detail(exc)) from exc

def _scoped(query: dict, scope: str | None) -> dict:
    """Restrict an Elasticsearch query to one jurisdiction (None = no restriction)."""
    if scope is None:
        return query
    return {"bool": {"must": [query], "filter": [{"term": {"jurisdiction.keyword": scope}}]}}


@app.get("/api/v1/search/firs")
async def search_firs(q: str, current_user: dict = Depends(get_current_user)):
    try:
        scope = search_scope(current_user)
        text_query = {"multi_match": {"query": q, "fields": ["station", "complainant", "accused", "description"]}}
        response = es_client.search(index="argus-firs", query=_scoped(text_query, scope))
        hits = [hit["_source"] for hit in response["hits"]["hits"]]
        log_action(current_user, action="search_firs", resource=f"search:{q[:80]}",
                   extra={"jurisdiction_filter": scope, "results": len(hits)})
        return {"status": "success", "query": q, "count": len(hits), "results": hits}
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e

@app.get("/api/v1/search/judges-view")
async def get_judge_targets(prison: str = "All", current_user: dict = Depends(get_current_user)):
    try:
        if prison == "All" or not prison:
            query = {"match_all": {}}
        else:
            query = {"match": {"prison_facility": prison}}

        scope = search_scope(current_user)
        response = es_client.search(
            index="argus-firs",
            query=_scoped(query, scope),
            size=50
        )
        
        hits = [hit["_source"] for hit in response["hits"]["hits"]]
        
        for hit in hits:
            if "aadhaar" in hit:
                hit["aadhaar"] = "[Aadhaar Redacted]"

        log_action(current_user, action="search_judges_view", resource=f"prison:{prison[:80]}",
                   extra={"jurisdiction_filter": scope, "results": len(hits)})
        return {"status": "success", "count": len(hits), "results": hits}
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e

@app.get("/api/v1/network/accused")
async def get_accused_network(
    accused_name: str,
    contact_limit: int = 15,
    case_id: str | None = Query(default=None),
    justification: str | None = Query(default=None),
    before: str | None = Query(default=None, description="ISO timestamp/date snapshot cutoff"),
    current_user: dict = Depends(get_current_user),
):
    try:
        enforce_case_access(case_id, justification, current_user)
        scope = search_scope(current_user)
        fir_query = """
        MATCH (s:Suspect {id: $name})-[:LINKED_TO_FIR]->(f:FIR)
        WHERE $before IS NULL OR f.date <= $before
        RETURN s.id AS accused, collect({id: f.id, jurisdiction: f.jurisdiction}) AS firs
        """
        # The suspect's own handsets and who those handsets call. Without this
        # the person view is only a star of FIRs, with no path into the call
        # network that the rest of the analytics is built on.
        phone_query = """
        MATCH (s:Suspect {id: $name})-[:USES_PHONE]->(p:Phone)
        RETURN collect(DISTINCT p.id) AS phones
        """
        contact_query = """
        MATCH (s:Suspect {id: $name})-[:USES_PHONE]->(p:Phone)-[c:CALLED]-(other:Phone)
        WHERE $before IS NULL OR c.timestamp <= $before
        WITH p.id AS source, other.id AS target, count(c) AS calls
        ORDER BY calls DESC
        LIMIT $limit
        RETURN source, target, calls
        """
        with neo4j_driver.session() as session:
            record = session.run(fir_query, name=accused_name, before=before).single()
            all_firs = record["firs"] if record else []

            if scope is not None:
                # A scoped officer only sees a person through FIRs in their own jurisdiction. Someone who exists
                # only elsewhere gets the same answer as someone who does not exist, so this can't be used to
                # probe what lies outside the officer's scope (and no 403 that would confirm it).
                firs = [f["id"] for f in all_firs if f["jurisdiction"] == scope]
                if not firs:
                    log_action(current_user, action="view_accused_network", resource=f"accused:{accused_name[:80]}",
                               extra={"jurisdiction_filter": scope, "in_scope_firs": 0})
                    return {"status": "success", "message": "No results in your jurisdiction."}
            else:
                firs = [f["id"] for f in all_firs]

            phones = session.run(phone_query, name=accused_name).single()
            phones = phones["phones"] if phones else []
            contacts = [
                {"source": r["source"], "target": r["target"], "calls": r["calls"]}
                for r in session.run(contact_query, name=accused_name, limit=contact_limit, before=before)
            ]

            log_action(current_user, action="view_accused_network", resource=f"accused:{accused_name[:80]}",
                       extra={"jurisdiction_filter": scope, "in_scope_firs": len(firs)})
            if firs or phones:
                return {
                    "status": "success",
                    "accused": accused_name,
                    "total_firs": len(firs),
                    "linked_firs": firs,
                    "phones": phones,
                    "phone_contacts": contacts,
                }
            return {"status": "success", "message": "No connections found in graph."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e


@app.get("/api/v1/network/path")
async def get_network_path(
    source: str,
    target: str,
    case_id: str | None = Query(default=None),
    justification: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    enforce_case_access(case_id, justification, current_user)
    query = """
    MATCH path = shortestPath((a {id: $source})-[*..6]-(b {id: $target}))
    WITH nodes(path) AS path_nodes, relationships(path) AS path_edges
    RETURN
      [n IN path_nodes | {
        id: (CASE WHEN head(labels(n)) = 'Suspect' THEN 'person' ELSE toLower(head(labels(n))) END) + ':' + coalesce(n.id, elementId(n)),
        label: coalesce(n.id, elementId(n)),
        type: CASE WHEN head(labels(n)) = 'Suspect' THEN 'person' ELSE toLower(head(labels(n))) END
      }] AS nodes,
      [r IN path_edges | {
        id: elementId(r),
        source: (CASE WHEN head(labels(startNode(r))) = 'Suspect' THEN 'person' ELSE toLower(head(labels(startNode(r)))) END) + ':' + startNode(r).id,
        target: (CASE WHEN head(labels(endNode(r))) = 'Suspect' THEN 'person' ELSE toLower(head(labels(endNode(r)))) END) + ':' + endNode(r).id,
        label: type(r),
        direct: true
      }] AS edges
    """
    with neo4j_driver.session() as session:
        record = session.run(query, source=source, target=target).single()
    if not record:
        raise HTTPException(status_code=404, detail="No path found within six hops.")
    log_action(
        current_user,
        action="find_network_path",
        resource=f"{source}->{target}",
        justification=justification,
    )
    return {"status": "success", "source": source, "target": target, "nodes": record["nodes"], "edges": record["edges"]}

@app.get("/api/v1/network/phone")
async def get_phone_network(
    phone_number: str,
    case_id: str | None = Query(default=None),
    justification: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    try:
        enforce_case_access(case_id, justification, current_user)
        query = """
        MATCH (p:Phone {id: $phone})-[c:CALLED]-(connected:Phone)
        RETURN connected.id AS connected_phone, c.duration AS duration, c.timestamp AS timestamp
        """
        # A handset with no call records can still be attributed to suspects.
        # Returning only CALLED edges made such phones look unconnected even
        # when the graph knew exactly who used them.
        user_query = """
        MATCH (s:Suspect)-[:USES_PHONE]->(p:Phone {id: $phone})
        RETURN collect(DISTINCT s.id) AS users
        """
        with neo4j_driver.session() as session:
            result = session.run(query, phone=phone_number)
            connections = [{"connected_phone": row["connected_phone"], "duration": row["duration"], "timestamp": row["timestamp"]} for row in result]
            users_row = session.run(user_query, phone=phone_number).single()
            users = users_row["users"] if users_row else []

            return {
                "status": "success",
                "phone": phone_number,
                "total_connections": len(connections),
                "connections": connections,
                "users": users,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e

@app.get("/api/v1/network/financial")
async def get_financial_network(
    account_id: str,
    case_id: str | None = Query(default=None),
    justification: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Return direct transaction partners for a FinancialAccount node."""
    try:
        enforce_case_access(case_id, justification, current_user)
        query = """
        MATCH (a:FinancialAccount {id: $account_id})-[t:TRANSACTED_WITH]-(partner:FinancialAccount)
        RETURN partner.id AS partner_id, t.amount AS amount, t.direction AS direction,
               t.timestamp AS timestamp, t.transaction_id AS transaction_id
        ORDER BY t.timestamp DESC
        LIMIT 50
        """
        # Look for suspects whose name appears in FIRs alongside this account
        suspect_query = """
        MATCH (s:Suspect)-[:LINKED_TO_FIR]->(f:FIR)
        WHERE f.id CONTAINS $account_id OR s.id CONTAINS $account_id
        RETURN collect(DISTINCT s.id) AS suspects
        """
        with neo4j_driver.session() as session:
            result = session.run(query, account_id=account_id)
            transactions = [
                {
                    "partner_id": row["partner_id"],
                    "amount": row["amount"],
                    "direction": row["direction"],
                    "timestamp": row["timestamp"],
                    "transaction_id": row["transaction_id"],
                }
                for row in result
            ]
            suspects_row = session.run(suspect_query, account_id=account_id).single()
            suspects = suspects_row["suspects"] if suspects_row else []
        log_action(
            current_user,
            action="view_financial_network",
            resource=f"account:{account_id}",
            justification=justification,
        )
        return {
            "status": "success",
            "account_id": account_id,
            "total_transactions": len(transactions),
            "transactions": transactions,
            "linked_suspects": suspects,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e


@app.get("/api/v1/analytics/burners")
async def detect_burner_phones(threshold: Optional[int] = None, current_user: dict = Depends(get_current_user)):
    """Phones with unusually high outgoing volume. `threshold` overrides the adaptive top-percentile cutoff."""
    try:
        query = """
        MATCH (p:Phone)-[c:CALLED]->(target:Phone)
        WITH p, count(c) as call_count
        RETURN p.id AS suspect_phone, call_count
        ORDER BY call_count DESC
        """
        with neo4j_driver.session() as session:
            rows = [(row["suspect_phone"], row["call_count"]) for row in session.run(query)]
        cutoff = threshold if threshold is not None else burner_cutoff([n for _, n in rows])
        burners = [{"phone": phone, "calls": n} for phone, n in rows if n >= cutoff]
        return {
            "status": "success",
            "threshold": cutoff,
            "total_flagged": len(burners),
            "burners": burners
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e

@app.get("/api/v1/search/master-dossier")
async def get_master_dossier(query: str, current_user: dict = Depends(get_current_user)):
    try:
        scope = search_scope(current_user)
        dossier_query = {
            "bool": {
                "should": [
                    {"term": {"fir_id.keyword": query}},
                    {"term": {"accused.keyword": query}},
                    {"match": {"fir_id": query}}
                ],
                "minimum_should_match": 1
            }
        }
        if scope is not None:
            dossier_query["bool"]["filter"] = [{"term": {"jurisdiction.keyword": scope}}]
        es_response = es_client.search(index="argus-firs", query=dossier_query, size=1)
        es_hits = [hit["_source"] for hit in es_response["hits"]["hits"]]
        log_action(current_user, action="view_dossier", resource=f"dossier:{query[:80]}",
                   extra={"jurisdiction_filter": scope, "results": len(es_hits)})

        for hit in es_hits:
            if "aadhaar" in hit:
                hit["aadhaar"] = "[Aadhaar Redacted]"

        return {
            "status": "success",
            "query": query,
            "total_matches": len(es_hits),
            "dossier_records": es_hits
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=_safe_detail(e)) from e

@app.post("/api/v1/biometric/enroll")
async def enroll_suspect(
    name: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("admin", "investigator")),
):
    try:
        content = await file.read()
        nparr = np.frombuffer(content, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        img_small = cv2.resize(img, (320, 320))

        embedding_objs = await asyncio.to_thread(_represent, img_small)
        embedding = np.array(embedding_objs[0]["embedding"], dtype=np.float32)

        if embedding.shape[0] != embedding_dimension:
            embedding = np.resize(embedding, (embedding_dimension,))

        faiss.normalize_L2(embedding.reshape(1, -1))
        suspect_hash = str(uuid.uuid4())
        face_index.add(embedding, {"hash_id": suspect_hash, "name": name, "fir_id": "N/A"})

        return {"status": "success", "suspect_hash": suspect_hash, "note": "ARCFACE + RETINAFACE (SPEED OPTIMIZED)"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Allowed base directory for portal enrollment — prevents path traversal
_ENROLL_BASE_DIR = Path(os.getenv("BIOMETRIC_BASE_DIR", "/app/uploads/enroll")).resolve()

@app.post("/api/v1/biometric/bulk-portal-enroll")
async def bulk_portal_enroll(
    folder_path: str = Form(...),
    current_user: dict = Depends(require_role("admin", "investigator")),
):
    try:
        path = (_ENROLL_BASE_DIR / folder_path).resolve()
        # Prevent directory traversal outside allowed base
        if not str(path).startswith(str(_ENROLL_BASE_DIR)):
            raise HTTPException(status_code=400, detail="Invalid folder path.")
        if not path.exists() or not path.is_dir():
            return {"status": "error", "message": "Provided directory path does not exist."}
        
        count = 0
        extensions = [".jpg", ".jpeg", ".png"]
        
        for file_path in path.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                suspect_name = file_path.parent.name if file_path.parent != path else file_path.stem
                try:
                    img = cv2.imread(str(file_path))
                    img_small = cv2.resize(img, (320, 320))

                    embedding_objs = await asyncio.to_thread(_represent, img_small)
                    embedding = np.array(embedding_objs[0]["embedding"], dtype=np.float32)
                    if embedding.shape[0] != embedding_dimension:
                        embedding = np.resize(embedding, (embedding_dimension,))

                    faiss.normalize_L2(embedding.reshape(1, -1))
                    suspect_hash = str(uuid.uuid4())
                    face_index.add(embedding, {"hash_id": suspect_hash, "name": suspect_name, "fir_id": "N/A"})
                    count += 1
                except Exception:
                    continue

        return {
            "status": "success",
            "total_indexed": count,
            "message": f"Massive dataset integrated successfully. Total vectors in FAISS: {face_index.ntotal}"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/v1/biometric/hunt")
async def hunt_suspect(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    try:
        content = await file.read()
        nparr = np.frombuffer(content, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        img_small = cv2.resize(img, (320, 320))

        embedding_objs = await asyncio.to_thread(_represent, img_small)
        embedding = np.array(embedding_objs[0]["embedding"], dtype=np.float32)
        
        if embedding.shape[0] != embedding_dimension:
            embedding = np.resize(embedding, (embedding_dimension,))

        faiss.normalize_L2(embedding.reshape(1, -1))
        
        best = face_index.search(embedding)
        if best:
            similarity_score, matched_suspect = best
            if similarity_score > 0.40:
                return {
                    "status": "success",
                    "match_found": True,
                    "confidence_score": similarity_score * 100,
                    "suspect_data": matched_suspect
                }

        return {"status": "success", "match_found": False}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

_MAX_ZIP_UNCOMPRESSED_MB = 500
_MAX_ZIP_MEMBERS = 1000

@app.post("/api/v1/biometric/bulk-upload-zip")
async def bulk_upload_zip(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("admin", "investigator")),
):
    temp_dir = f"temp_zip_{uuid.uuid4()}"
    os.makedirs(temp_dir, exist_ok=True)
    zip_path = os.path.join(temp_dir, "uploaded_dataset.zip")

    try:
        content = await file.read()
        with open(zip_path, "wb") as buffer:
            buffer.write(content)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            members = zip_ref.infolist()
            if len(members) > _MAX_ZIP_MEMBERS:
                raise HTTPException(status_code=400, detail=f"ZIP contains too many files (>{_MAX_ZIP_MEMBERS}).")
            total_size = sum(m.file_size for m in members)
            if total_size > _MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"ZIP uncompressed size exceeds {_MAX_ZIP_UNCOMPRESSED_MB} MB limit.")
            # Extract with path sanitisation to prevent zip-slip
            for member in members:
                member_path = Path(os.path.normpath(os.path.join(temp_dir, member.filename)))
                if not str(member_path).startswith(os.path.abspath(temp_dir)):
                    continue  # Skip any traversal attempt
            zip_ref.extractall(temp_dir)
        
        count = 0
        extensions = [".jpg", ".jpeg", ".png"]
        
        for root, _, files in os.walk(temp_dir):
            for file_name in files:
                if any(file_name.lower().endswith(ext) for ext in extensions):
                    file_path = os.path.join(root, file_name)
                    suspect_name = os.path.splitext(file_name)[0] 
                    
                    try:
                        img = cv2.imread(file_path)
                        if img is None:
                            continue
                        
                        img_small = cv2.resize(img, (320, 320))

                        embedding_objs = await asyncio.to_thread(_represent, img_small)
                        embedding = np.array(embedding_objs[0]["embedding"], dtype=np.float32)
                        if embedding.shape[0] != embedding_dimension:
                            embedding = np.resize(embedding, (embedding_dimension,))

                        faiss.normalize_L2(embedding.reshape(1, -1))
                        suspect_hash = str(uuid.uuid4())
                        face_index.add(embedding, {"hash_id": suspect_hash, "name": suspect_name, "fir_id": "N/A"})
                        count += 1
                    except Exception:
                        continue
                        
        return {
            "status": "success", 
            "total_indexed": count, 
            "message": f"Massive ZIP Dataset extracted FASTER. Total vectors: {face_index.ntotal}"
        }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

@app.post("/api/v1/biometric/unified-enroll")
async def unified_enroll(
    fir_id: str = Form(...),
    accused: str = Form(...),
    mobile: str = Form(...),
    aadhaar: str = Form(...),
    dob: str = Form(...),
    history: str = Form(...),
    prison: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("admin", "investigator")),
):
    try:
        content = await file.read()
        
        file_ext = Path(file.filename).suffix or ".jpg"
        img_filename = f"{uuid.uuid4()}{file_ext}"
        img_local_path = os.path.join("uploads", img_filename)
        with open(img_local_path, "wb") as f_out:
            f_out.write(content)
            
        _public_base = os.getenv("PUBLIC_API_BASE_URL", "http://localhost:8000")
        image_url = f"{_public_base}/api/v1/biometric/image/{img_filename}"

        nparr = np.frombuffer(content, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        img_small = cv2.resize(img, (320, 320))

        embedding_objs = await asyncio.to_thread(_represent, img_small)
        embedding = np.array(embedding_objs[0]["embedding"], dtype=np.float32)
        if embedding.shape[0] != embedding_dimension:
            embedding = np.resize(embedding, (embedding_dimension,))
        
        faiss.normalize_L2(embedding.reshape(1, -1))
        suspect_hash = str(uuid.uuid4())
        face_index.add(embedding, {"hash_id": suspect_hash, "name": accused, "fir_id": fir_id})

        record = {
            "fir_id": fir_id,
            "accused": accused,
            "mobile": mobile,
            "aadhaar": aadhaar,
            "dob": dob,
            "criminal_history": history,
            "prison_facility": prison,
            "date": "2026-09-08",
            "station": "ARGUS Command",
            # The enrolling officer's own jurisdiction; unscoped roles (admin...) leave it Unassigned so it is not
            # attributed to a district by accident.
            "jurisdiction": current_user["jurisdiction"] if search_scope(current_user) else UNASSIGNED,
            "description": f"Target manually enrolled. History: {history}",
            "Image": image_url 
        }
        es_client.index(index="argus-firs", id=fir_id, document=record)

        with neo4j_driver.session() as session:
            session.run(
                """
                MERGE (s:Suspect {id: $suspect_id})
                MERGE (f:FIR {id: $fir_id})
                SET f.jurisdiction = $jurisdiction
                MERGE (s)-[:LINKED_TO_FIR]->(f)
                """,
                suspect_id=accused,
                fir_id=fir_id,
                jurisdiction=record["jurisdiction"],
            )

        return {"status": "success", "message": "Unified profile created successfully across all grids!"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.delete("/api/v1/target/delete-by-fir")
async def delete_record_by_fir(
    fir_id: str,
    current_user: dict = Depends(require_role("admin")),
):
    try:
        es_response = es_client.search(
            index="argus-firs",
            query={"term": {"fir_id.keyword": fir_id}},
            size=1
        )
        hits = es_response["hits"]["hits"]
        if not hits:
            return {"status": "error", "message": f"No record found with FIR ID: {fir_id}"}
        
        record_source = hits[0]["_source"]
        accused_name = record_source.get("accused")
        image_url = record_source.get("Image", "")

        es_client.delete(index="argus-firs", id=hits[0]["_id"])

        suspect_removed = False
        with neo4j_driver.session() as session:
            session.run(
                """
                MATCH (f:FIR {id: $fir_id})
                DETACH DELETE f
                """,
                fir_id=fir_id
            )
            if accused_name:
                # Only drop the suspect if this was their last FIR; otherwise they still belong to other cases.
                removed = session.run(
                    """
                    MATCH (s:Suspect {id: $name})
                    WHERE NOT (s)-[:LINKED_TO_FIR]->(:FIR)
                    DETACH DELETE s
                    RETURN count(s) AS n
                    """,
                    name=accused_name
                ).single()
                suspect_removed = bool(removed and removed["n"])

        if "uploads/" in image_url or "/biometric/image/" in image_url:
            filename = os.path.basename(image_url)
            local_img_path = os.path.join("uploads", filename)
            if _IMAGE_NAME_RE.match(filename) and os.path.exists(local_img_path):
                os.remove(local_img_path)

        face_index.remove_by_fir(fir_id)

        return {
            "status": "success", 
            "suspect_removed": suspect_removed,
            "message": f"Record {fir_id} (Accused: {accused_name}) purged from Elasticsearch, Neo4j, face index and storage."
                       + ("" if suspect_removed else " The suspect is kept because other FIRs still reference them.")
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
