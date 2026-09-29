# ARGUS MVP API Contract v1

The canonical contract is [`openapi-mvp-v1.json`](./openapi-mvp-v1.json), generated
from the FastAPI application at `/openapi.json`.

This contract is frozen for the hackathon MVP. Endpoint changes require updating
the application and regenerating this file in the same change.

## Alignment changes

- Added explicit `pending`, `processing`, `quarantined`, and `processed` job states.
- Added `POST /api/v1/jobs/{job_id}/retry` for admin/supervisor reprocessing.
- Dataset uploads now queue before parsing so malformed and unsupported files
  receive a durable job and human-readable quarantine reason.
- Search and graph infrastructure failures now return HTTP 503 instead of
  HTTP 200 error-shaped payloads that the frontend could mistake for empty data.
- Search, graph, burner, dossier, and surveillance reads now require a valid
  bearer token; graph calls can include `case_id` and `justification` so
  sensitive-case access is enforced and audited outside the case workspace.
- Audit queries support `user`, `action`, `start`, and `end` filters.
- Confirmed graph contract is `/api/v1/network/accused` and
  `/api/v1/network/phone`; no `/api/v1/graph/expand` endpoint exists in MVP v1.
- Confirmed burner analytics returns `burners[].phone` and `burners[].calls`.

## Known intentional MVP boundaries

- Text extraction is exposed separately through `/api/v1/ingest/text` and its
  normalized candidates are queued on `argus_graph_queue`; the structured FIR/CDR
  worker consumes `argus_ingest_queue`.
- Report export is PDF-only in v1 and returns an attachment.
- Added `GET /api/v1/network/path` for a six-hop Neo4j shortest-path result.
- Added the optional `before` query parameter to `GET /api/v1/network/accused` for
  date-bounded graph snapshots.
- Added financial transaction ingestion for filenames containing `financial` or
  `transaction`; the worker writes `FinancialAccount` nodes and
  `TRANSACTED_WITH` relationships.
- Added a PostgreSQL-backed tamper-evident audit hash chain and admin
  verification endpoint at `GET /api/v1/audit/verify`. This is not an external
  blockchain; blockchain anchoring remains a production roadmap item.

## Jurisdiction scoping and entity-resolution review

- `GET /api/v1/cases`, case detail, pin/note, case-scoped graph calls and report
  export are now limited to the caller's jurisdiction for the `investigator` role
  (403 otherwise, audited). `admin`, `supervisor` and `analyst` keep cross-jurisdiction
  oversight. Creating a case outside your own jurisdiction is also 403 for investigators.
- `GET /api/v1/resolve/check` no longer returns `auto_merge`; every candidate is
  `resolution: "review"` with `suggested: "merge" | "possible_match"`.
- Added `POST /api/v1/resolve/decision` (`confirm_merge` | `reject`) and
  `GET /api/v1/resolve/decisions`. A confirmed merge creates a reversible
  `ALIAS_OF` edge in Neo4j; identities are never merged destructively.

## Production hardening (phase 1)

- `POST /auth/login` now returns 429 after `LOGIN_MAX_FAILURES` (5) failed attempts per
  employee ID for `LOGIN_LOCKOUT_SECONDS` (15 min). Failed and locked-out attempts are audited.
- Requests with `Content-Length` above `MAX_REQUEST_BYTES` (100 MB) get 413.
- All responses carry `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`.
  CORS methods/headers are restricted to what the UI uses.
- `audit_log` is append-only at the database level (UPDATE/DELETE/TRUNCATE are rejected,
  except filling a NULL `record_hash`).
- With `ARGUS_ENV` not dev/local the API refuses to start if JWT_SECRET < 32 chars,
  SEED_DEMO_USERS=true, CORS contains `*`, or REDIS_PASSWORD is unset. Token lifetime defaults to 60 min outside dev.
- The public `/uploads` static mount is removed. Suspect photos are served only via
  `GET /api/v1/biometric/image/{filename}` (bearer token required, access audited).
  New enrollments store this URL in `Image`; older records still hold `/uploads/...` URLs, which no longer resolve.

## Performance
- `/patterns` and `/analytics/*` no longer block the event loop; centrality is cached (`CENTRALITY_CACHE_SECONDS`, 300s),
  betweenness is sampled on large graphs (`BETWEENNESS_SAMPLE`, 150), and the pattern list is cached per worker for `PATTERNS_CACHE_SECONDS` (60s).
  `/patterns` went from ~30s to ~2.7s cold, ~15ms warm.

## Pattern thresholds
- Burner detection is adaptive: phones in the top `1 - BURNER_PERCENTILE` (5%) of outgoing call volume, never below
  `BURNER_MIN_CALLS` (5). `GET /analytics/burners` now returns the `threshold` used; `?threshold=N` still overrides it.
- `central_network_hub` patterns now appear: nodes with PageRank at least 3x the median (top 5), confidence relative to the top hub.

## Biometrics
- The face index is persisted (`FACE_INDEX_DIR`) and shared by all workers; it survives restarts. Inference no longer blocks the event loop.
- `DELETE /target/delete-by-fir` also removes the suspect's face from the index.
- Frontend biometric calls use a 120s timeout (model cold start).
- `DELETE /target/delete-by-fir` now looks the record up by exact FIR ID and removes the Suspect node only when no other
  FIR still links to it; the response includes `suspect_removed`.

## Two-step verification (built-in TOTP, no external identity provider)
- `POST /auth/login` returns `{mfa_required, enrollment_required, mfa_token}` instead of a session when the account has MFA enabled
  or its role is listed in `MFA_REQUIRED_ROLES`. Accounts without MFA are unaffected. `mfa_token` is a 5-minute step token
  and is rejected everywhere else.
- New: `POST /auth/mfa/verify`, `POST /auth/mfa/enroll/begin`, `POST /auth/mfa/enroll/complete` (sign-in steps);
  `GET /auth/mfa/status`, `POST /auth/mfa/setup|enable|disable` (self-service); `POST /admin/users/{employee_id}/mfa-reset` (admin, audited).
- Authenticator-app codes (RFC 6238, SHA-1, 6 digits, 30 s, ±1 step) with replay protection; 8 one-time recovery codes (stored hashed).
  TOTP secrets are Fernet-encrypted at rest with `MFA_ENCRYPTION_KEY` (required outside dev). Wrong codes share the login lockout counter.
- Config: `MFA_REQUIRED_ROLES` (e.g. `admin,supervisor`), `MFA_ENCRYPTION_KEY`, `MFA_ISSUER`.

## Dashboard support
- `GET /dashboard/summary` returns real dataset counts (FIRs, suspects, phones, accounts, calls, transactions, sightings), cached 60 s.
- `POST /alerts/{alert_id}/read` and `POST /alerts/read-all` mark alerts read (previously unread counts could never go down).

## Biometric performance
- API container: 4 CPUs and TensorFlow/BLAS thread limits (`OMP_NUM_THREADS`, `TF_NUM_INTRAOP_THREADS`, ...) matched to the quota. The host reports 12 cores, so
  the defaults oversubscribed the 2-CPU quota and throttled inference. A face embedding went from ~40 s to ~7 s. RetinaFace detection is still ~85% of that time;
  a lighter detector (needs its weights available offline) is the next step.
