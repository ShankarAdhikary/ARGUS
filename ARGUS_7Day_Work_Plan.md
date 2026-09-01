# ARGUS — 7-Day Build Plan (6-Person Team)

## Scoping note (read this first)

The PRD/TRD describe a national-scale, government-certified production system (Kubernetes clusters, CCTNS/ICJS/NATGRID integrations, STQC/CERT-In certification, 100M-node graphs). None of that is buildable in 7 days, and none of it should be attempted — no real CCTNS/Aadhaar/CDR integration in a hackathon build.

What's realistic and demo-worthy in 7 days:
- A **synthetic dataset** standing in for FIRs/CDRs/financial records (you generate or scrape sample data — never real case data).
- A **single-node** Postgres + Neo4j + Elasticsearch stack (Docker Compose, not K8s clustering).
- A **simplified pipeline**: upload → NER → basic entity resolution (rule/threshold-based, not a fully tuned Splink model) → graph write.
- **One working end-to-end flow**: search → entity detail → network graph → one pattern-detection example → report export.
- Auth with basic RBAC (skip HSM/FIDO2/hash-chained audit — a simple append-only table is enough to demonstrate the concept).

Everything below is scoped to that MVP. Treat the PRD/TRD/Schema docs as the reference for "what this becomes later," not the Day 1–7 checklist.

---

## Team & Role Assignment

| # | Person | Role | Primary Docs to Own |
|---|---|---|---|
| 1 | **P1** | Data Ingestion & Pipeline Engineer | TRD §4 (pipeline), Backend Schema §2.5, §6 |
| 2 | **P2** | ML/NLP Engineer | TRD §2.4, PRD §5 (features 2–3, 7), Backend Schema §2.6 |
| 3 | **P3** | Graph & Database Engineer | Backend Schema §2–4, Architecture §2.6 |
| 4 | **P4** | Backend API Engineer (Auth/Case/Reports) | Backend Schema §2.1–2.3, §2.7, §5; TRD §6 (scaled down) |
| 5 | **P5** | Frontend Engineer — Core App Shell | UI/UX Brief §1–4, §6–7; App Flow §2.1–2.4, §2.7–2.9 |
| 6 | **P6** | Frontend Engineer — Graph & Analytics UI | UI/UX Brief §5.3–5.4; App Flow §2.5–2.6, §2.8 |

Backend (P1–P4) and Frontend (P5–P6) work against a shared, frozen API contract agreed **before end of Day 1** so both sides can build in parallel without blocking each other.

---

## Day-by-Day Plan

### Day 1 — Setup & Contracts (all hands)
Goal: everyone can run the stack locally and knows exactly what API/schema they're building to.

- **P1**: Stand up Docker Compose with Postgres 16, Neo4j (single instance), Elasticsearch, MinIO, Redis. Write seed/synthetic data generator (fake FIRs, CDR-style call logs, transactions) — 50–100 sample entities to start.
- **P2**: Set up Python NLP environment (spaCy + a pretrained NER model). Identify or fine-tune a quick entity extractor for person/phone/location/vehicle from the synthetic FIR text. Skip IndicBERT/regional-language work for now — English-only MVP, note it as a stretch goal.
- **P3**: Implement Postgres schema from Backend Schema §2.1–2.5 (trim unused columns like Aadhaar tokenization, PostGIS geography if not needed for demo). Implement Neo4j node/relationship schema from §3.
- **P4**: Scaffold FastAPI project. Define and freeze the REST API contract (endpoints for search, entity detail, graph expand, case CRUD, report export, login) as an OpenAPI spec — this is the Day 1 deliverable everyone else needs.
- **P5**: Scaffold React + TypeScript + Tailwind app. Implement design tokens/colors/typography from UI/UX Brief §2–3. Build login screen shell.
- **P6**: Spike Sigma.js (or Cytoscape.js) with a hardcoded sample graph to confirm rendering approach and node/edge styling per the brief before building real integration.

**End-of-day sync:** confirm the API contract is frozen. Any changes after this cost double.

### Day 2 — Ingestion → Extraction Pipeline
- **P1**: Build ingestion endpoint (file upload → MinIO + Kafka/Redis-queue event, skip Kafka if time-tight — a simple task queue is fine for a demo). Implement checksum-based dedup.
- **P2**: Wire NER output into a staging table. Produce confidence scores per extracted entity/field.
- **P3**: Write the entity-write path: staging → `entities`/`relationships` tables → Neo4j sync (start with a direct write, CDC/Debezium is overkill for 7 days).
- **P4**: Implement auth (JWT issuance, basic RBAC middleware — role + jurisdiction claim check). Implement `users`, `jurisdictions`, `cases` CRUD endpoints.
- **P5**: Build the Dashboard and Entity Search Results screens against mock data (per API contract), per UI/UX Brief §5.1–5.2.
- **P6**: Build the Network Visualization shell (toolbar, filter rail, canvas) against mock graph data, per UI/UX Brief §5.3.

### Day 3 — Entity Resolution & Case Workspace
- **P1**: Add a second and third synthetic source type (CDR-style, financial-style records) so relationships have variety. Quarantine-queue handling for malformed records.
- **P2**: Implement simplified entity resolution: fuzzy-name matching (e.g. `rapidfuzz`) + phone/vehicle exact-match, with a confidence threshold (≥0.92 auto-merge, else flagged) per Backend Schema §6. This is the trickiest piece — start early, keep it rule-based rather than a full Splink model.
- **P3**: Implement graph query endpoints: entity neighborhood expansion (N-hop), shortest path between two entities.
- **P4**: Implement Case Workspace endpoints (pin entity, notes, case-entity links) and the append-only audit log table + write-on-every-access logic.
- **P5**: Build Entity Detail screen (Overview/Relationships/Timeline/Notes tabs) per App Flow §2.4, wired to real API.
- **P6**: Wire the Network Visualization to real `/graph/expand` endpoint. Implement node click → detail panel, double-click → expand.

### Day 4 — Integration Checkpoint + Patterns
Goal: first full vertical slice works end-to-end (upload → extract → resolve → graph → search → view).

- **All**: Morning integration session — run the full pipeline on synthetic data together, fix breakages.
- **P1**: Fix ingestion bugs found in integration; add a second batch of synthetic data (aim for 500+ entities so the graph looks non-trivial).
- **P2**: Implement one pattern-detection example — pick the easiest to demo credibly: e.g. "recurring co-location" (shared location + overlapping timeframe) via simple graph queries, or a financial-transaction cluster via Isolation Forest on synthetic transaction amounts. One well-executed pattern beats three shallow ones.
- **P3**: Implement Neo4j GDS centrality (PageRank or Betweenness) on the demo graph — surfaced as an "influence score" field.
- **P4**: Implement `/patterns` endpoints (list, detail, confirm/dismiss) and confidence-badge data contract used across screens.
- **P5**: Build Case Workspace screen (Overview tab, pinned entities, sub-nav) per App Flow §2.7.
- **P6**: Build Pattern Detection Results + Pattern Detail screens per UI/UX Brief §5.4.

### Day 5 — Reports, Alerts, Polish
- **P1**: Add ingestion status/health surface (simple admin view of quarantined/processed records) — supports the Admin Console demo.
- **P2**: Tune the pattern-detection thresholds against the synthetic data so the demo produces a believable, explainable result (not random noise). Write the "why flagged" explanation text.
- **P3**: Optimize slow Cypher queries found during Day 4 integration; add indexes per Backend Schema §4.
- **P4**: Implement Report Builder export endpoint (PDF or DOCX with entity list, graph snapshot placeholder, confidence badges, citations) and Alerts endpoint (entity-watch rule → notification record).
- **P5**: Build Report Builder flow (scope → sections → preview → export) per App Flow §2.8. Build Alerts screen per §2.9.
- **P6**: Add timeline scrubber to the graph view (date-range filter re-renders graph) — this is a strong demo moment, worth the time.

### Day 6 — Sensitive-Case Handling, RBAC Enforcement, Bug Bash
- **P1 & P3**: Load-test the pipeline with a larger synthetic batch (aim for a few thousand records) to make sure the demo doesn't crawl live.
- **P2**: Add the confidence-conflict UI case (e.g., two DOBs from two sources) as a small demo-able edge case per App Flow §4.
- **P4**: Implement sensitive-case gating (`is_sensitive` flag → justification prompt → audit entry) — a small feature that visibly demonstrates the compliance story, which matters a lot for judging.
- **P5**: Sensitive-case banner, confidence pills, source-citation chips — component polish per UI/UX Brief §6. Accessibility pass (contrast, labels).
- **P6**: Polish graph interactions (hover state, right-click context menu, "Find Path" between two selected nodes).
- **All**: Cross-team bug bash in the afternoon — everyone tests everyone else's flow.

### Day 7 — Demo Prep & Buffer
- **Morning**: Final bug fixes only. No new features.
- **P1–P4**: Prepare a clean, reset demo dataset that tells a coherent story (e.g., "trace this trafficking-recruitment network end to end") — this matters more than raw feature count for judging.
- **P5–P6**: Rehearse the click-path for the demo; make sure it's smooth without needing a live coding save.
- **All**: Full dry run (at least twice) with someone playing "judge" and asking questions about scalability/security — have short, honest answers ready (e.g., "in production this would run on the state's MeghRaj cloud with Keycloak MFA and hash-chained audit logs, as detailed in our TRD — here we've implemented the core logic on a single-node stack for the demo").
- **Buffer time** in the afternoon for whatever breaks during rehearsal — there's always something.

---

## Cross-Cutting Rules for the Week

1. **Freeze the API contract Day 1.** Backend and frontend build in parallel against it; changes after Day 1 go through a quick sync, not silent breakage.
2. **Synthetic data only.** Never load real personal data, even "just for testing."
3. **Daily 15-minute standup** — what's blocked, what's done, any contract changes.
4. **One vertical slice working by Day 4**, even if ugly — it's far better to demo a narrow, working end-to-end path than six half-built features on Day 7.
5. **Keep a running list of "what we'd add for production"** mapped to the PRD/TRD sections you skipped (regional-language NER, hash-chained audit, K8s scaling, real government integrations). Judges tend to reward teams that clearly know the gap between demo and production.
