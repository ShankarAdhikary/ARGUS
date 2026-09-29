# ARGUS — 24-Hour Build Plan

## Current status (updated 2026-09-29)

**The MVP is complete and verified end to end** (backend, all 15 screens, browser-tested for every role). The plan below is kept as the original build record; its "starting point" and API-contract sections describe the state at kickoff, not today.

Done since the original plan:
- Every contract endpoint is live (auth, cases, patterns, centrality/communities, alerts, reports, audit incl. hash-chain verify, resolve, admin health). The frontend no longer uses any mock fallback.
- Public Home page and a reworked sign-in page; role-based menus; sensitive-case justification gate; investigator jurisdiction scoping.
- Entity resolution needs a human decision (`/resolve/decision`); confirmed links are reversible `ALIAS_OF` edges.
- Burner detection is adaptive (top 5% of callers); network-hub patterns work; `/patterns` is cached (30s -> ~3s cold).
- Security hardening: startup guard for unsafe production config, login lockout, append-only audit table, security headers, request-size cap, authenticated photo endpoint (no public `/uploads`).
- Face index is now persistent and shared across workers (`FACE_INDEX_DIR`, default `uploads/face_index`), inference runs off the event loop, models warm up at startup, and deleting a suspect also removes their face. `scripts/rebuild_face_index.py` restores the index from Elasticsearch.
- Network graph readability (leaf FIRs collapse behind a toggle) and a working timeline snapshot.

Still open (production, not MVP): MFA/SSO, TLS + encryption at rest, secrets vault, Postgres RLS and jurisdiction scoping of search/graph, local Hindi/regional NER, real CCTNS/ICJS/NATGRID connectors, Kubernetes/HA, load testing, DOCX reports, Hindi UI, CERT-In/STQC audits.

Known caveats: the `ingestion_pipeline` integration tests mutate data and were not run against the demo stack; two integration tests skip by design (need the AML seed / regex-only mode).

Running tests: unit and integration suites must run in separate processes (the unit conftest stubs the database).
`docker exec argus-pipeline-api-1 python -m pytest tests/unit` and
`docker exec -e ARGUS_LIVE_TESTS=1 argus-pipeline-api-1 python -m pytest tests/integration/test_live_api.py tests/integration/test_phase2_analytics.py`
(install `pytest httpx` in the container first).

---

## Starting point (as of now)

**Backend (`ARGUS-Pipeline`, FastAPI) — furthest along:**
- Docker Compose: Postgres, Neo4j, Elasticsearch, MinIO, Redis, api, worker — all running.
- Ingestion (`/api/v1/ingest`, `/api/v1/ingest/text`) with checksum dedup, Redis queue, worker writes to ES + Neo4j.
- LLM zero-shot entity extraction (Groq + regex fallback), Pydantic-validated.
- Search (`/search/firs`, `/search/master-dossier`, `/search/judges-view`) over Elasticsearch.
- Basic Neo4j queries: accused network, phone network, burner-phone heuristic.
- A fully-built facial recognition subsystem (DeepFace/ArcFace + FAISS): enroll, bulk enroll, hunt, bulk ZIP, unified enroll, delete. Not in the original docs — a bonus feature, already working.
- **Postgres is running but unused by any code.** No auth, no cases, no audit log, no reports, no alerts, no entity resolution, no centrality.

**Frontend (`ARGUS-Frontend`, React+TS+Vite):**
- One 134-line `App.tsx` — an API test console (upload, extract, search, job status). No router, no design tokens, none of the 10 planned screens, no graph visualization, no biometric UI despite the backend supporting it.

## Team & roles (2–3 people)

| Role | Person | Owns |
|---|---|---|
| **A — Backend/Platform** | 1 | Auth/RBAC, cases, audit log, reports, alerts, entity resolution, centrality/patterns |
| **B — Frontend Core** | 1 | App shell, router, login, dashboard, search, entity detail, case workspace, reports/alerts UI |
| **C — Frontend Graph/Data** *(if 3rd person; else folded into B, backend-heavy items go to A)* | 1 | Network graph (Cytoscape), pattern results UI, biometric "Hunt" UI, demo data curation, integration testing |

If only 2 people: **A** stays backend-only; **B** absorbs all of C's frontend work but **drops** the graph timeline scrubber and admin console polish first if time runs short (see Cut List).

## Ground rules
1. **API contract frozen at Hour 1** (list below). Changes after that go through a 2-minute sync, not silent breakage.
2. Every new backend feature ships **thin but real** — a working endpoint with 3 fields beats a modeled-but-broken one with 15.
3. Synthetic data only.
4. Standup every 4 hours, 5 minutes: what's blocked, what's done.
5. First full vertical slice (login → search → entity detail → graph) must work by Hour 9. Everything after that is additive.

---

## API contract to freeze at Hour 1 (Person A builds these; B/C code against them immediately, using mocked JSON until real)

```
POST /api/v1/auth/login          {employee_id, password} -> {access_token, role, full_name}
GET  /api/v1/auth/me             -> {user_id, role, full_name}

GET  /api/v1/cases               -> list
POST /api/v1/cases               {title, fir_number, jurisdiction, is_sensitive, sensitivity_reason}
GET  /api/v1/cases/{id}          (if is_sensitive: requires ?justification=... or 428 response)
POST /api/v1/cases/{id}/entities {entity_type, entity_value}
POST /api/v1/cases/{id}/notes    {content}

GET  /api/v1/analytics/centrality        -> networkx PageRank/betweenness over Suspect+Phone graph
GET  /api/v1/patterns                    -> unified list: burners + centrality outliers, each with confidence + explanation
POST /api/v1/patterns/{id}/feedback      {verdict: useful|false_positive}

GET  /api/v1/resolve/check?name=X        -> fuzzy candidate matches (rapidfuzz) against existing Suspects

POST /api/v1/alerts/rules                {entity_value}
GET  /api/v1/alerts

POST /api/v1/reports/export              {case_id, sections[]} -> PDF bytes

GET  /api/v1/audit                       -> admin only
GET  /api/v1/admin/ingestion-health      -> queued/processed/failed counts
```

Everything already built (`/health`, `/ingest`, `/ingest/text`, `/search/*`, `/network/*`, `/analytics/burners`, `/biometric/*`) stays as-is.

---

## Hour-by-hour schedule

### Hour 0–1 — Kickoff
- All: read this plan, agree on the contract above, split tasks, confirm everyone can run `docker-compose up`.
- **A**: add `python-jose`/`PyJWT`, `rapidfuzz`, `fpdf2` (or `reportlab`), `psycopg2-binary` to `requirements.txt`. Stand up the 4 Postgres tables needed (`users`, `cases`, `case_entity_links`, `case_notes`, `audit_log`, `alert_rules`, `alerts`) — trimmed straight from Backend Schema §2.1–2.7, skip anything not needed for demo (Aadhaar tokenization, PostGIS, jurisdiction hierarchy — just a flat jurisdiction string).
- **B**: add `react-router-dom`, scaffold routes for the 8 screens below (empty shells), build the color/typography tokens from UI/UX Brief §2–3 directly into `styles.css` (skip installing Tailwind — not worth the setup time in 24h).
- **C**: add `cytoscape` (+ `react-cytoscapejs` if available, else vanilla), spike it against a hardcoded sample graph; start curating the demo dataset (a coherent "trafficking recruitment network" story using `seed_data.py` + hand-written narrative FIRs) — needed by everyone by Hour 17.

### Hour 1–9 — Sprint 1 (build the vertical slice)
**A:**
- `POST /auth/login` + `GET /auth/me` — seed 4 users (investigator/analyst/supervisor/admin) directly in Postgres, JWT with role claim, FastAPI dependency `require_role(...)`.
- Audit log table + a `log_action()` helper called from every read/write endpoint (user_id, action, resource, justification, timestamp) — this single helper is reused everywhere, do it early.
- Case CRUD (`POST/GET /cases`, `POST /cases/{id}/entities`, `POST /cases/{id}/notes`).
- `GET /resolve/check` — rapidfuzz against existing `Suspect` node names in Neo4j, threshold 0.92 auto vs. flagged.

**B:**
- App shell: nav bar, role-aware landing, protected routes (redirect to login if no token).
- Login screen wired to real `/auth/login`.
- Dashboard: case list + quick search bar.
- Search Results screen wired to `/search/firs` and `/search/master-dossier` — entity cards with confidence pill + source count.

**C:**
- Entity Detail screen: Overview tab (ES record + redacted fields), Relationships tab (calls `/network/accused` or `/network/phone` depending on entity type), stub Timeline/Notes tabs.
- Keep iterating the Cytoscape spike into a reusable `<NetworkGraph>` component (nodes color-coded by type per UI/UX Brief §2, edge style solid/dashed for direct/inferred).

### Hour 9–9:30 — Checkpoint 1 (all hands)
Run the full path together: login → search → entity detail → basic graph render. Fix breakages before moving on.

### Hour 9:30–17:30 — Sprint 2
**A:**
- `GET /analytics/centrality` — pull Suspect+Phone subgraph from Neo4j, build a `networkx` graph (already a dependency), run `pagerank` and `betweenness_centrality`, return ranked list with scores.
- `GET /patterns` — merge `/analytics/burners` output + centrality outliers into one list with `pattern_type`, `confidence`, `explanation` string ("5 calls between these numbers within 2 hours, matching burner signature"). `POST /patterns/{id}/feedback` just appends to a table.
- `POST /reports/export` — `fpdf2`-generated PDF: case title, pinned entities, confidence-tagged findings, source citations. Simple layout, not fancy.
- `POST /alerts/rules` + `GET /alerts` — on every ingest/extraction, check new entity values against active rules, insert an alert row if matched.

**B:**
- Case Workspace screen: overview, pinned entities (from `case_entity_links`), notes, sensitive-case banner + justification prompt when `is_sensitive`.
- Wire Network Graph component (from C) into the Entity Detail and Case Workspace screens — node click → mini detail panel, double-click → expand via `/network/*`.

**C:**
- Pattern Detection Results screen: cards from `/patterns`, confidence pill, `[Confirm] [Dismiss]` buttons wired to feedback endpoint. Pattern Detail view = mini subgraph + explanation text.
- Biometric "Hunt" UI: this backend feature already works and is a strong demo differentiator — build a simple screen: upload photo → `/biometric/hunt` → show match + confidence + linked FIR. Also expose `/biometric/unified-enroll` as an "Add Suspect" form.

### Hour 17:30–18 — Checkpoint 2
Cross-test: everyone tries everyone else's flow. Freeze remaining scope — no new endpoints after this.

### Hour 18–22 — Sprint 3 (coverage + polish)
**A:**
- `GET /admin/ingestion-health` (counts by status from the jobs already in Redis/Postgres), `GET /audit` (admin-only, paginated).
- Sensitive-case gating end-to-end: any case access without `?justification=` on a sensitive case returns 428 with a clear message; audit-log every access attempt.
- Bug fixes surfaced from checkpoints; help load synthetic data.

**B:**
- Report Builder flow: scope selection → section checkboxes → preview → call `/reports/export`, trigger download.
- Alerts screen: list + "create rule" form.
- Admin Console stub: ingestion health table + audit log viewer (admin role only).

**C:**
- Confidence pills, source-citation chips, sensitive-case banner — component polish per UI/UX Brief §6.
- Load the curated demo dataset (trafficking-network story) through the real pipeline; verify the graph tells a coherent story end to end.
- Accessibility pass: labels, contrast, no icon-only buttons.

### Hour 22–23:30 — Bug bash + dry run
- All: run the full demo script twice, someone plays "judge."
- Fix only what's broken in the core path. Do not add features.
- Prepare 2–3 sentence honest answers for "what's production vs. demo" (regional-language NER, hash-chained audit, K8s, real CCTNS/Aadhaar integration — all correctly deferred per the docs).

### Hour 23:30–24 — Buffer
Final fixes only. Reset the demo dataset to a clean state.

---

## Cut list (drop in this order if behind schedule)

1. Admin console / audit log viewer UI (keep the backend endpoint, skip the screen — mention it's there).
2. Alerts screen (keep the endpoint, demo via API call if needed).
3. Timeline scrubber on the graph.
4. Report export as PDF → fall back to a printable HTML preview (browser print-to-PDF).
5. Entity resolution UI → keep it backend-only, narrate it instead of clicking through it.
6. Centrality → keep burner-detection as the sole pattern-detection example (already exists).

**Never cut:** login → search → entity detail → network graph → case pin → one pattern example. That's the vertical slice the whole plan protects.

## Demo script (aim for ~5 minutes)
1. Log in as investigator → dashboard shows active cases.
2. Search a suspect name → entity detail → relationships tab shows network.
3. Explore network graph, click into a linked phone number, show burner-phone flag.
4. Pin entities to a case, add a note, show the sensitive-case justification prompt.
5. Run "Hunt" with a photo → face match → linked FIR (biometric bonus feature).
6. Generate a report from the case → export.
7. Close with the honest "what's demo vs. production" list.

---
## UX & accessibility pass (2026-09-29)
- Type scale enforced (12/13/14/16px+, nothing below 12px), secondary text raised to >=4.5:1 contrast, fonts self-hosted (works offline / on isolated networks). Audited with a script across all pages: 0 sub-12px text, 0 low-contrast text.
- Dashboard rebuilt: clickable overview tiles, "needs attention" ordering, real dataset counts (`/dashboard/summary`), grouped alerts with relative times and Mark read / Mark all read, recent + live "key people" search shortcuts, per-section loading/error/retry, role-aware shortcuts (analysts no longer see ingest actions the server would reject).
- Every page now has its own title; all inputs labelled; phone layout with a slide-in menu (there was previously no navigation on phones); fixed sideways scroll on Alerts; removed the hard-coded "Threat level" badge.
- 46 scripted UI flow checks pass (tiles, search, filters, views, entity tabs/pin/watch, graph controls/export, case create/note/tabs, patterns, alerts, hunt/ingestion guards, security).
