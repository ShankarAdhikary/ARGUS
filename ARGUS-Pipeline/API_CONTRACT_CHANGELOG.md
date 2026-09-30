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
- `GET /api/v1/analytics/wsrs-leaderboard` rows now also carry `fir_count`, `jurisdictions` and `computed_at`; the response has a top-level `computed_at`.
- `POST /api/v1/resolve/check`, `POST /api/v1/resolve/decision` and `GET /api/v1/resolve/decisions` are now jurisdiction-scoped for scoped roles (candidates outside scope are not returned; an out-of-scope name in a decision answers 404; decision history shows the caller's own decisions only). `decisions` accepts `limit` (1-100).
- New frontend pages: `/analytics/wsrs` (supervisor, admin), `/biometric/voiceprint`, `/resolve`.

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

## Jurisdiction scoping of search and graph (FR-10)
- FIR records now carry a `jurisdiction` (Elasticsearch document and Neo4j `FIR` node), derived from the station via `jurisdictions.py`
  (synthetic demo mapping — replace with the real police-station master). New records get it at ingest; existing ones via
  `scripts/backfill_jurisdiction.py`. Stations with no mapping are `Unassigned` and visible only to unscoped roles.
- `GET /search/firs`, `/search/master-dossier`, `/search/judges-view` filter to the caller's jurisdiction for `investigator`
  (admin, supervisor and analyst are unscoped, matching case access).
- `GET /network/accused` lists only in-scope FIRs for scoped callers. If none are in scope the answer is
  `{"status":"success","message":"No results in your jurisdiction."}` — identical to a person who does not exist, never a 403.
- `log_action(..., extra=...)` stores structured `details` on audit rows (included in the hash only when present, so old rows still verify);
  these endpoints record `jurisdiction_filter` and the result count. `GET /audit` returns `details`.
- NOT yet scoped: `/network/phone`, `/network/financial`, `/network/path`, `/analytics/*`, `/patterns`, `/alerts` and the biometric hunt.

## Women-safety pattern intelligence
- `GET /patterns` now also returns `repeat_offender_recurrence` (suspects linked to 3+ FIRs) and `co_accused_cluster` (pairs named together in 2+ FIRs).
  The former `burner_phone_cluster` type is renamed `phone_cluster_hub` (ids keep the `burner-` prefix so saved analyst verdicts stay valid).
- New optional fields: `women_safety_flag`, `women_safety_fir_count`, `risk_tier` (HIGH/MEDIUM for the two new types), `entity_ids`.
- `women_safety_flag` is evidence-based: true only when at least `WOMEN_SAFETY_MIN_FIRS` (3; 2 for clusters) of the underlying FIRs — and at least
  `WOMEN_SAFETY_MIN_SHARE` (30%) — are trafficking / exploitation-of-persons cases (FIR `network = trafficking`, or text about minors/women/stalking/etc.).
  The `explanation` states the count and share either way.
- The two new pattern queries respect jurisdiction scoping (investigators only see recurrence within their own jurisdiction). Hub, financial and
  centrality patterns are still unscoped.
- Frontend: removed a legacy block that added ~200 synthetic "suspected burner" cards on the Patterns page; verdicts on phone hubs now persist from the detail page.

## Production hardening (phase 2)

**Word reports.** `POST /reports/export?format=pdf|docx` (default `pdf`). The `.docx` has the case metadata table, the selected sections, an
"AI-derived lead — verify before use" line under every pattern, and an audit footer ("Exported by … at …, justification: …"). Served as
`Content-Disposition: attachment; filename=argus-report-{case_id}.docx`; the export is audited with `details.format`. Both formats now list only
patterns that involve the case's pinned entities (previously every pattern in the system). Unknown formats return 422; the sensitive-case gate (428) applies to both.

**Row-level security (Postgres).** `cases`, `case_notes` and `case_entity_links` have RLS enabled and forced. The service connects as the database superuser,
which ignores RLS, so each authenticated request's transaction runs `SET LOCAL ROLE argus_rls` (a NOLOGIN role created at start-up) with `app.role` and
`app.jurisdiction` set from the caller; the policy admits a row when its jurisdiction matches or the role is admin/supervisor/analyst (the same
`CROSS_JURISDICTION_ROLES` tuple the API uses). No context = no rows for the restricted role (fail closed); start-up, login and the ingest worker run as the
service user. Behaviour change: another district's case now answers **404** to a scoped officer (it used to be 403), so its existence is not revealed.
`get_cursor` now always ends its transaction (it used to leave read-only transactions open on pooled connections). Disable with `ENABLE_DB_RLS=false`.

**Hindi NER fallback.** When no LLM answers and the text contains Devanagari, `extract_candidates` can use `ai4bharat/IndicNER`
(`extraction_method: "indic_ner"`; PER/ORG/LOC mapped to person/organization/location, plus phone numbers with Devanagari digits normalised).
Off by default (`ENABLE_INDIC_NER=false`). The dependencies (`transformers`, `torch`) live in `requirements-indic.txt` and are installed only with
`docker compose build --build-arg INSTALL_INDIC_NER=true api` because they add gigabytes to the image. Not yet exercised against the real model.

**Keycloak / OIDC.** With `KEYCLOAK_URL` set, `Authorization: Bearer` tokens must be access tokens from that realm: signature checked against the realm's
JWKS (asymmetric algorithms only), plus issuer, audience (`KEYCLOAK_AUDIENCE`) and expiry. Roles come from `realm_access.roles` (most privileged of
admin/supervisor/analyst/investigator wins; none = 403); `preferred_username`, `name` and a `jurisdiction` claim fill the rest. ARGUS's own tokens are then
rejected and `/auth/login` plus the MFA endpoints answer 400. Unset (the default) leaves everything as it was. The web app does not yet perform the OIDC redirect flow.

**Server.** `uvicorn --timeout-keep-alive 65` (the 5 s default dropped idle connections that clients were about to reuse: `RemoteDisconnected` on the first request after a pause).

**Load test.** `tests/load/locustfile.py` (locust). 50 users / 60 s against the demo stack: 6,450 requests, 0 failures, search p95 ≈ 180 ms (target 3,000 ms).

## Production build additions (post-MVP; `openapi-mvp-v1.json` has not been regenerated for these)

New endpoints, all JWT-authenticated, jurisdiction-scoped where they read graph data, and audit-logged:

- `GET /api/v1/network/charges?person_name=` - charge history as a subgraph (LegalCharge nodes).
- `GET /api/v1/analytics/charge-patterns` - IPC/BNS co-charge pairs and matrix.
- `GET /api/v1/analytics/repeat-victims` - supervisor/admin; aggregate, pseudonymous.
- `GET /api/v1/analytics/wsrs?person_name=`, `GET /api/v1/analytics/wsrs-leaderboard` (supervisor/admin),
  `POST /api/v1/analytics/wsrs/recompute` (admin) - Women Safety Risk Score with factor breakdown.
- `GET /api/v1/analytics/hotspots?category=`, `GET /api/v1/analytics/risk-forecast?jurisdiction=` (analyst and above).
- `GET /api/v1/evidence/ledger?case_id=`, `GET /api/v1/evidence/verify/{file_id}` - evidence chain of custody.
- `POST /api/v1/ingest/voice` - offline Whisper transcription; returns transcript, entities, suggested FIR fields.

Changed behaviour: `POST /api/v1/ingest` accepts optional `case_id` / `justification` form fields and now writes an
evidence-ledger row and an audit entry. `/ingest/text` responses gain `legal_sections` and per-entity `canonical`/`aliases`.
`/resolve/check` matches across scripts (`match_type: transliteration_match`). `/network/phone`, `/network/financial`,
`/network/path` and `/analytics/burners` are jurisdiction-scoped for scoped roles; an out-of-scope node answers like an
unknown one. The API is no longer published on port 8000: it is reached through nginx over TLS.

## Fingerprint identification

- `GET /api/v1/biometric/fingerprint/status`, `POST /api/v1/biometric/fingerprint/match` (any role, scoped to the caller's
  jurisdiction), `POST /api/v1/biometric/fingerprint/enroll` and `POST /api/v1/biometric/fingerprint/bulk-enroll-zip`
  (supervisor/admin). All are audit-logged (probe/image SHA-256, print type, top candidate).
- Engine: SourceAFIS 3.18 (Java, Apache-2.0) through a JPype bridge, enabled with
  `FINGERPRINT_ENGINE=fingerprint:JvmSourceAFISEngine`. With no engine configured the endpoints answer 501 with an explanation.
  The image carries a JRE and 14 jars pinned by SHA-256 (`vendor/sourceafis/`).
- `match` takes `print_type` (`rolled` | `latent`). Labels: score >= 70 High, 40-69 Medium, below 40 Low / insufficient; for a
  latent probe the label adds "/ latent match". **Scores are SourceAFIS scores, not a 0-100 scale** (a genuine same-finger pair
  scored ~235 and an identical print ~548), and they are not comparable with scores from other AFIS products.
- A print that fails the quality gate (blank, blurred or washed-out image, or too few minutiae) is refused with **422** and a
  `quality_check` body, before any matching. `nfiq_score` is always `null`: no NFIQ/NFIQ2 is computed; `quality_score` names its
  `method` (`variance+ridge-frequency` image gate, `minutiae-count`, or the lower of the two).

## Face biometrics: audit and jurisdiction scoping

- `POST /biometric/hunt` now writes an audit entry for every search (hit, miss or error: probe SHA-256, jurisdiction filter,
  top match, similarity) and only considers faces enrolled in the caller's jurisdiction. It accepts optional `case_id` and
  `justification` form fields, like the fingerprint match.
- `POST /biometric/enroll`, `/unified-enroll`, `/bulk-portal-enroll` and `/bulk-upload-zip` audit-log the enrolment and stamp the
  face with a jurisdiction (the enrolling officer's own; Unassigned for unscoped roles). Faces enrolled before this change have
  none and count as Unassigned (visible only to unscoped roles): run `scripts/backfill_face_jurisdiction.py` to resolve them
  from their FIR.

## Production start-up guards

- The API refuses to start with `ARGUS_ENV=production` while any stored account still uses a published demo password
  (`demo_guard.py`; fails closed if the check cannot run). `scripts/preflight.py` also refuses when compose would seed the demo
  accounts (`SEED_DEMO_USERS` defaults to true, so production must set it to false explicitly).

## Voice biometrics (speaker identification): built, switched OFF

Design: `docs/voice-biometrics-design.md`. The endpoints below are **not registered** (404) unless `VOICEPRINT_ENABLED=true`, and
production refuses to start with it enabled and no calibration file. They must stay off until `scripts/evaluate_voiceprint.py` has
been run on real recordings.

- `GET /api/v1/biometric/voice/status`, `POST .../match` (any role, jurisdiction-scoped), `POST .../enroll` (supervisor/admin).
- **Every enrolment and match requires `lawful_interception_ref`** (the authorization order number). A missing or placeholder
  reference is refused with 422 and recorded as a `voiceprint_denied` audit entry; accepted ones are stored on the voiceprint and
  in the audit row. The system cannot verify the order exists: it is an accountability record.
- No threshold is hard-coded. Without a calibration file the response is ranking-only: `calibrated: false`, `match_found: null`,
  every label `Uncalibrated: ranking only`. With one, only candidates at or above the measured 0.1% false-accept threshold are
  listed. Every result is labelled "Investigative lead — requires forensic voice expert confirmation before use in proceedings."
- Audio below the minimum net speech (3 s) is refused with 422. Raw audio is never stored, only a 192-d embedding and the sample's SHA-256.

## Fingerprint calibration (per print type)

- `scripts/evaluate_fingerprint.py` scores every probe against the rolled gallery offline, through the same engine, quality gate and
  minutiae floors the service uses, and prints per mode (`rolled`, `latent` = real lifts, `latent_crop` = stand-ins) the false-accept
  and false-reject rates for scores 20-100 in steps of 5, with a 95% upper bound on the false-accept rate. Different fingers of one
  person are reported as their own impostor group. `--dry-run` never writes.
- It writes `uploads/fingerprint-calibration.json` only for a mode with >= 100 fingers, >= 100 genuine and >= 10,000 impostor trials
  (latent also >= 50 real lifts; crops never count) and only with `--provenance` naming the data source. Otherwise it exits 2.
- The service reads that file at start-up (`FINGERPRINT_CALIBRATION_FILE`): a calibrated mode uses its measured operating threshold
  and bands instead of 40 / 70; an uncalibrated mode keeps the defaults. `GET /biometric/fingerprint/status` reports
  `thresholds` and `calibrated` per print type; `POST .../match` returns `threshold_used` and `calibrated`.
- The voice and fingerprint evaluations now share their statistics (`evalstats.py`).

