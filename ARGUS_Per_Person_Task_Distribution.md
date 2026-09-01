# ARGUS — 7-Day Build: Per-Person Task Distribution

Reorganized from `ARGUS_7Day_Work_Plan.md` — same plan, grouped by person instead of by day, so each teammate can see their own path end-to-end.

**Scoping reminder:** the PRD/TRD/Architecture docs describe an 18-month, national-scale, government-certified system. None of that is buildable in 7 days. This plan is scoped to a single-node demo stack with synthetic data only — treat the full docs as "what this becomes later," not the Day 1–7 checklist.

**Team & primary doc ownership**

| # | Person | Role | Primary docs to own |
|---|---|---|---|
| 1 | P1 | Data Ingestion & Pipeline Engineer | TRD §4, Backend Schema §2.5 & §6 |
| 2 | P2 | ML/NLP Engineer | TRD §2.4, PRD §5 (features 2–3, 7), Backend Schema §2.6 |
| 3 | P3 | Graph & Database Engineer | Backend Schema §2–4, Architecture §2.6 |
| 4 | P4 | Backend API Engineer (Auth/Case/Reports) | Backend Schema §2.1–2.3 & §2.7 & §5, TRD §6 (scaled down) |
| 5 | P5 | Frontend Engineer — Core App Shell | UI/UX Brief §1–4 & §6–7, App Flow §2.1–2.4 & §2.7–2.9 |
| 6 | P6 | Frontend Engineer — Graph & Analytics UI | UI/UX Brief §5.3–5.4, App Flow §2.5–2.6 & §2.8 |

Backend (P1–P4) and Frontend (P5–P6) build in parallel against a shared API contract frozen by end of Day 1.

---

## P1 — Data Ingestion & Pipeline Engineer

| Day | Task | Notes |
|---|---|---|
| 1 | Docker Compose stack: Postgres 16, Neo4j (single instance), Elasticsearch, MinIO, Redis. Write seed/synthetic data generator (fake FIRs, CDR-style call logs, transactions) | 50–100 sample entities to start |
| 2 | Ingestion endpoint: file upload → MinIO + Kafka/Redis-queue event. Checksum-based dedup | Skip Kafka if time-tight — a simple task queue is fine for a demo |
| 3 | Add a 2nd and 3rd synthetic source type (CDR-style, financial-style records) so relationships have variety. Quarantine-queue handling for malformed records | |
| 4 | **AM: full-team integration session** — run the pipeline end to end together, fix breakages. Fix ingestion bugs found; add a 2nd batch of synthetic data | Target 500+ entities so the graph looks non-trivial |
| 5 | Ingestion status/health surface — simple admin view of quarantined/processed records | Supports the Admin Console demo |
| 6 | *(with P3)* Load-test the pipeline with a larger synthetic batch | Aim for a few thousand records so the demo doesn't crawl live |
| 7 | *(with P2–P4)* Prepare a clean, reset demo dataset that tells a coherent story | e.g. "trace this trafficking-recruitment network end to end" |

---

## P2 — ML/NLP Engineer

| Day | Task | Notes |
|---|---|---|
| 1 | Python NLP environment (spaCy + a pretrained NER model). Identify/fine-tune a quick entity extractor for person/phone/location/vehicle from synthetic FIR text | English-only MVP; regional-language (IndicBERT) is a stretch goal |
| 2 | Wire NER output into a staging table. Produce confidence scores per extracted entity/field | |
| 3 | Implement simplified entity resolution: fuzzy-name matching (`rapidfuzz`) + phone/vehicle exact match, confidence threshold (≥0.92 auto-merge, else flagged) | Trickiest piece — start early, keep it rule-based rather than a full Splink model |
| 4 | Implement one pattern-detection example — pick the easiest to demo credibly (e.g. "recurring co-location" via simple graph queries, or a financial-transaction cluster via Isolation Forest on synthetic amounts) | One well-executed pattern beats three shallow ones |
| 5 | Tune pattern-detection thresholds against the synthetic data for a believable, explainable result. Write the "why flagged" explanation text | |
| 6 | Add the confidence-conflict UI case (e.g. two DOBs from two sources) as a small demo-able edge case | |
| 7 | *(with P1,P3,P4)* Help prepare the demo dataset | |

---

## P3 — Graph & Database Engineer

| Day | Task | Notes |
|---|---|---|
| 1 | Implement Postgres schema from Backend Schema §2.1–2.5 (trim unused columns — Aadhaar tokenization, PostGIS geography — if not needed for the demo). Implement Neo4j node/relationship schema from §3 | |
| 2 | Entity-write path: staging → `entities`/`relationships` tables → Neo4j sync | Start with a direct write; CDC/Debezium is overkill for 7 days |
| 3 | Graph query endpoints: N-hop neighborhood expansion, shortest path between two entities | |
| 4 | **AM: full-team integration session.** Neo4j GDS centrality (PageRank or Betweenness) on the demo graph, surfaced as an "influence score" field | |
| 5 | Optimize slow Cypher queries found during Day 4 integration; add indexes per Backend Schema §4 | |
| 6 | *(with P1)* Load-test the pipeline with a larger synthetic batch | |
| 7 | *(with P1,P2,P4)* Help prepare the demo dataset | |

---

## P4 — Backend API Engineer (Auth / Case / Reports)

| Day | Task | Notes |
|---|---|---|
| 1 | Scaffold FastAPI project. Define and freeze the REST API contract (search, entity detail, graph expand, case CRUD, report export, login) as an OpenAPI spec | **This is the Day 1 deliverable everyone else needs — confirm frozen by EOD** |
| 2 | Implement auth (JWT issuance, basic RBAC middleware — role + jurisdiction claim check). Implement `users`, `jurisdictions`, `cases` CRUD endpoints | |
| 3 | Implement Case Workspace endpoints (pin entity, notes, case-entity links) and the append-only audit log table + write-on-every-access logic | |
| 4 | **AM: full-team integration session.** Implement `/patterns` endpoints (list, detail, confirm/dismiss) and the confidence-badge data contract used across screens | |
| 5 | Implement Report Builder export endpoint (PDF or DOCX with entity list, graph snapshot placeholder, confidence badges, citations) and the Alerts endpoint | |
| 6 | Implement sensitive-case gating (`is_sensitive` flag → justification prompt → audit entry) | Small feature that visibly demonstrates the compliance story — matters a lot for judging |
| 7 | *(with P1–P3)* Help prepare the demo dataset | |

---

## P5 — Frontend Engineer, Core App Shell

| Day | Task | Notes |
|---|---|---|
| 1 | Scaffold React + TypeScript + Tailwind app. Implement design tokens/colors/typography from UI/UX Brief §2–3. Build login screen shell | |
| 2 | Build Dashboard and Entity Search Results screens against mock data | UI/UX Brief §5.1–5.2 |
| 3 | Build Entity Detail screen (Overview/Relationships/Timeline/Notes tabs), wired to real API | App Flow §2.4 |
| 4 | **AM: full-team integration session.** Build Case Workspace screen (Overview tab, pinned entities, sub-nav) | App Flow §2.7 |
| 5 | Build Report Builder flow (scope → sections → preview → export). Build Alerts screen | App Flow §2.8–2.9 |
| 6 | Sensitive-case banner, confidence pills, source-citation chips — component polish. Accessibility pass (contrast, labels) | UI/UX Brief §6 |
| 7 | *(with P6)* Rehearse the click-path for the demo — smooth, no live coding needed | |

---

## P6 — Frontend Engineer, Graph & Analytics UI

| Day | Task | Notes |
|---|---|---|
| 1 | Spike Sigma.js (or Cytoscape.js) with a hardcoded sample graph to confirm rendering approach and node/edge styling per the brief | |
| 2 | Build the Network Visualization shell (toolbar, filter rail, canvas) against mock graph data | UI/UX Brief §5.3 |
| 3 | Wire the Network Visualization to the real `/graph/expand` endpoint. Node click → detail panel, double-click → expand | |
| 4 | **AM: full-team integration session.** Build Pattern Detection Results + Pattern Detail screens | UI/UX Brief §5.4 |
| 5 | Add a timeline scrubber to the graph view (date-range filter re-renders graph) | Strong demo moment, worth the time |
| 6 | Polish graph interactions (hover state, right-click context menu, "Find Path" between two selected nodes) | |
| 7 | *(with P5)* Rehearse the click-path for the demo | |

---

## All-hands sync points (protect these on the calendar)

| When | What |
|---|---|
| End of Day 1 | API contract frozen — every other role builds against it. Changes after this cost double |
| Morning of Day 4 | Full-team integration session — first working end-to-end vertical slice (upload → extract → resolve → graph → search → view) |
| Afternoon of Day 6 | Cross-team bug bash — everyone tests everyone else's flow |
| Day 7 | Two full dry runs (someone plays "judge," asks about scalability/security); buffer time in the afternoon |

## Standing rules for the week

1. Freeze the API contract Day 1. Changes after that go through a quick sync, not silent breakage.
2. Synthetic data only — never real personal data, even "just for testing."
3. Daily 15-minute standup: what's blocked, what's done, any contract changes.
4. One vertical slice working by Day 4, even if ugly.
5. Keep a running "what we'd add for production" list mapped to the PRD/TRD sections you skipped (regional-language NER, hash-chained audit, K8s scaling, real government integrations). Judges tend to reward teams that clearly know the gap between demo and production.
