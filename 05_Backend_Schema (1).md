# Backend Schema
## ARGUS — AI-Powered Criminal Network Analysis System

---

## 1. Data Architecture Overview

ARGUS uses a **polyglot persistence** model:
- **PostgreSQL** — system-of-record for users, cases, source documents metadata, audit logs, and structured entity attributes (relational integrity, transactional guarantees).
- **Neo4j** — the entity/relationship graph itself (optimized for traversal, centrality, and pattern queries).
- **Elasticsearch** — full-text search index over document content.
- **MinIO (object storage)** — raw source files (scanned FIRs, images, audio/video surveillance).

PostgreSQL is the authoritative store for identity, access control, and provenance; Neo4j is a derived, continuously-synced analytical view built from the same underlying entity/relationship records (with a `source_record_id` foreign key back to PostgreSQL on every graph relationship for citation and audit purposes).

---

## 2. PostgreSQL Schema (Core Tables)

### 2.1 Identity & Access

```sql
-- Users
users (
  user_id            UUID PRIMARY KEY,
  employee_id        VARCHAR(50) UNIQUE NOT NULL,
  full_name          VARCHAR(255) NOT NULL,
  email              VARCHAR(255) UNIQUE,
  role               VARCHAR(50) NOT NULL,      -- investigator | analyst | supervisor | admin | compliance
  jurisdiction_id    UUID REFERENCES jurisdictions(jurisdiction_id),
  clearance_tier     SMALLINT NOT NULL DEFAULT 1,  -- 1=basic ... 4=cross-agency intel
  mfa_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  status             VARCHAR(20) NOT NULL DEFAULT 'active', -- active | suspended | deactivated
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at      TIMESTAMPTZ
)

-- Jurisdictions (state/district/station hierarchy)
jurisdictions (
  jurisdiction_id    UUID PRIMARY KEY,
  name               VARCHAR(255) NOT NULL,
  level              VARCHAR(20) NOT NULL, -- national | state | district | station
  parent_id          UUID REFERENCES jurisdictions(jurisdiction_id)
)

-- Roles/permissions (fine-grained, supplements coarse `role` on users)
permissions (
  permission_id      UUID PRIMARY KEY,
  code               VARCHAR(100) UNIQUE NOT NULL   -- e.g. 'case.export', 'case.view_sensitive'
)

user_permissions (
  user_id            UUID REFERENCES users(user_id),
  permission_id      UUID REFERENCES permissions(permission_id),
  granted_by         UUID REFERENCES users(user_id),
  granted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, permission_id)
)
```

### 2.2 Cases & Investigations

```sql
cases (
  case_id            UUID PRIMARY KEY,
  fir_number         VARCHAR(100),
  title              VARCHAR(255) NOT NULL,
  jurisdiction_id    UUID REFERENCES jurisdictions(jurisdiction_id),
  status             VARCHAR(30) NOT NULL DEFAULT 'open',  -- open | under_review | closed
  is_sensitive       BOOLEAN NOT NULL DEFAULT FALSE,        -- triggers row-level security
  sensitivity_reason VARCHAR(100),                          -- e.g. 'minor_involved', 'witness_protection'
  category           VARCHAR(100),                          -- e.g. 'trafficking', 'cyber_harassment'
  opened_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at          TIMESTAMPTZ
)

case_assignments (
  case_id            UUID REFERENCES cases(case_id),
  user_id            UUID REFERENCES users(user_id),
  assigned_role      VARCHAR(50),  -- lead_investigator | analyst | reviewer
  assigned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, user_id)
)

case_entity_links (
  case_id            UUID REFERENCES cases(case_id),
  entity_id          UUID REFERENCES entities(entity_id),
  linked_by          UUID REFERENCES users(user_id),
  linked_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (case_id, entity_id)
)

case_notes (
  note_id            UUID PRIMARY KEY,
  case_id            UUID REFERENCES cases(case_id),
  author_id          UUID REFERENCES users(user_id),
  content             TEXT NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

### 2.3 Entities (mirrors graph nodes, PostgreSQL is source of truth for attributes)

```sql
entities (
  entity_id          UUID PRIMARY KEY,
  entity_type        VARCHAR(30) NOT NULL,  -- person | organization | location | vehicle | phone | financial_account | event
  canonical_name     VARCHAR(255),
  resolution_status  VARCHAR(20) NOT NULL DEFAULT 'unresolved', -- unresolved | auto_merged | analyst_confirmed
  confidence_score   NUMERIC(4,3),          -- 0.000–1.000, applies to resolution confidence
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)

entity_aliases (
  alias_id           UUID PRIMARY KEY,
  entity_id          UUID REFERENCES entities(entity_id),
  alias_value        VARCHAR(255) NOT NULL,
  source_record_id   UUID REFERENCES source_records(source_record_id)
)

-- Type-specific attribute tables (avoids sparse single wide table)
entity_person_attrs (
  entity_id          UUID PRIMARY KEY REFERENCES entities(entity_id),
  date_of_birth      DATE,
  gender             VARCHAR(20),
  nationality        VARCHAR(100),
  known_address      TEXT,
  identity_token     VARCHAR(255)   -- tokenized reference to Aadhaar/ID match, never raw ID number
)

entity_vehicle_attrs (
  entity_id          UUID PRIMARY KEY REFERENCES entities(entity_id),
  registration_number VARCHAR(20),
  make_model         VARCHAR(100),
  owner_entity_id    UUID REFERENCES entities(entity_id)
)

entity_phone_attrs (
  entity_id          UUID PRIMARY KEY REFERENCES entities(entity_id),
  msisdn             VARCHAR(20),
  operator           VARCHAR(50)
)

entity_financial_account_attrs (
  entity_id          UUID PRIMARY KEY REFERENCES entities(entity_id),
  account_number_hash VARCHAR(255),  -- hashed, not stored in plaintext
  institution        VARCHAR(100),
  account_type       VARCHAR(50)
)

entity_location_attrs (
  entity_id          UUID PRIMARY KEY REFERENCES entities(entity_id),
  address_text       TEXT,
  latitude           NUMERIC(9,6),
  longitude          NUMERIC(9,6),
  geom               GEOGRAPHY(POINT, 4326)  -- PostGIS
)

entity_event_attrs (
  entity_id          UUID PRIMARY KEY REFERENCES entities(entity_id),
  event_type         VARCHAR(100),
  event_datetime     TIMESTAMPTZ,
  location_entity_id UUID REFERENCES entities(entity_id)
)
```

### 2.4 Relationships (mirrored into Neo4j; PostgreSQL retains authoritative provenance)

```sql
relationships (
  relationship_id    UUID PRIMARY KEY,
  source_entity_id   UUID REFERENCES entities(entity_id),
  target_entity_id   UUID REFERENCES entities(entity_id),
  relationship_type  VARCHAR(50) NOT NULL,  -- called | transacted_with | co_accused | co_located | family_of | associated_with | owns
  is_direct          BOOLEAN NOT NULL,       -- direct (explicit record) vs. inferred (indirect)
  confidence_score   NUMERIC(4,3) NOT NULL,
  first_observed_at  TIMESTAMPTZ,
  last_observed_at   TIMESTAMPTZ,
  source_record_id   UUID REFERENCES source_records(source_record_id),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

### 2.5 Source Records & Ingestion

```sql
data_sources (
  source_id          UUID PRIMARY KEY,
  source_type        VARCHAR(50) NOT NULL,  -- fir | cdr | financial | surveillance | social_media | criminal_history | intel_agency
  system_name        VARCHAR(100),           -- e.g. 'CCTNS', 'ICJS'
  connector_config_id UUID
)

source_records (
  source_record_id   UUID PRIMARY KEY,
  source_id          UUID REFERENCES data_sources(source_id),
  external_ref_id    VARCHAR(255),           -- original system's record ID
  checksum           VARCHAR(64) NOT NULL,   -- for idempotent ingestion dedup
  raw_object_key     VARCHAR(500),           -- pointer to MinIO object (raw file)
  ingested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  ingestion_status   VARCHAR(20) NOT NULL DEFAULT 'processed', -- queued | processing | processed | quarantined
  jurisdiction_id    UUID REFERENCES jurisdictions(jurisdiction_id)
)
```

### 2.6 Patterns & Alerts

```sql
detected_patterns (
  pattern_id         UUID PRIMARY KEY,
  pattern_type       VARCHAR(100) NOT NULL,  -- suspicious_financial_cluster | recurring_co_location | recruitment_chain | anomalous_sequence
  confidence_score   NUMERIC(4,3) NOT NULL,
  description        TEXT,
  detected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  model_version      VARCHAR(50),
  status             VARCHAR(20) NOT NULL DEFAULT 'new'  -- new | confirmed | dismissed | escalated
)

pattern_entities (
  pattern_id         UUID REFERENCES detected_patterns(pattern_id),
  entity_id          UUID REFERENCES entities(entity_id),
  role_in_pattern    VARCHAR(50),
  PRIMARY KEY (pattern_id, entity_id)
)

pattern_feedback (
  feedback_id        UUID PRIMARY KEY,
  pattern_id         UUID REFERENCES detected_patterns(pattern_id),
  user_id            UUID REFERENCES users(user_id),
  verdict            VARCHAR(20) NOT NULL,  -- useful | false_positive
  comment            TEXT,
  submitted_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)

alert_rules (
  rule_id            UUID PRIMARY KEY,
  owner_user_id      UUID REFERENCES users(user_id),
  rule_type          VARCHAR(50),   -- entity_watch | pattern_subscription
  target_entity_id   UUID REFERENCES entities(entity_id),
  pattern_type_filter VARCHAR(100),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)

alerts (
  alert_id           UUID PRIMARY KEY,
  rule_id            UUID REFERENCES alert_rules(rule_id),
  triggered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload            JSONB,
  read_status        BOOLEAN NOT NULL DEFAULT FALSE
)
```

### 2.7 Audit Log (append-only, hash-chained)

```sql
audit_log (
  audit_id           BIGSERIAL PRIMARY KEY,
  user_id            UUID REFERENCES users(user_id),
  action             VARCHAR(100) NOT NULL,  -- view_entity | export_report | merge_entity | access_sensitive_case | login | login_failed
  resource_type      VARCHAR(50),
  resource_id        UUID,
  justification      TEXT,                    -- required for sensitive-case access / export
  ip_address         INET,
  occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  prev_hash          VARCHAR(64) NOT NULL,     -- hash of previous row, for tamper-evidence
  row_hash           VARCHAR(64) NOT NULL
)
-- audit_log table has INSERT-only grants; no UPDATE/DELETE permitted at the DB role level
```

---

## 3. Neo4j Graph Schema

### 3.1 Node Labels
- `:Person {entity_id, canonical_name, confidence_score}`
- `:Organization {entity_id, canonical_name}`
- `:Location {entity_id, address_text, latitude, longitude}`
- `:Vehicle {entity_id, registration_number}`
- `:Phone {entity_id, msisdn}`
- `:FinancialAccount {entity_id, account_number_hash, institution}`
- `:Event {entity_id, event_type, event_datetime}`

All nodes carry `entity_id` (matching PostgreSQL `entities.entity_id`) as the join key.

### 3.2 Relationship Types
- `(:Person)-[:CALLED {confidence, first_observed, last_observed, source_record_id}]->(:Phone)`
- `(:Person)-[:TRANSACTED_WITH {amount, currency, confidence, source_record_id}]->(:FinancialAccount)`
- `(:Person)-[:CO_ACCUSED {case_id, confidence}]->(:Person)`
- `(:Person)-[:CO_LOCATED_WITH {confidence, event_id}]->(:Person)`
- `(:Person)-[:ASSOCIATED_WITH {confidence, basis}]->(:Person)`  — general inferred link
- `(:Person)-[:OWNS]->(:Vehicle)`
- `(:Person)-[:PRESENT_AT]->(:Event)`
- `(:Event)-[:OCCURRED_AT]->(:Location)`
- `(:Organization)-[:LINKED_TO]->(:Person)`

### 3.3 Indexing Strategy (Neo4j)
- Unique constraint on `entity_id` for every node label.
- Full-text index on `canonical_name` (person, organization) for fuzzy lookup.
- Composite index on `(:FinancialAccount).institution + account_number_hash`.
- Range index on `Event.event_datetime` for temporal queries.
- Graph Data Science (GDS) in-memory projections refreshed on a schedule for centrality/community algorithms (PageRank, Louvain, Betweenness) rather than computed on the live transactional graph.

---

## 4. Indexing Strategy (PostgreSQL)

| Table | Index | Rationale |
|---|---|---|
| `entities` | B-tree on `entity_type`, GIN trigram on `canonical_name` | Fast type filtering + fuzzy name search |
| `entity_phone_attrs` | B-tree unique on `msisdn` | Phone lookup |
| `entity_vehicle_attrs` | B-tree unique on `registration_number` | Vehicle lookup |
| `relationships` | B-tree on `(source_entity_id)`, `(target_entity_id)`, `relationship_type` | Traversal support for hybrid queries before hitting Neo4j |
| `source_records` | Unique on `(source_id, checksum)` | Idempotent ingestion |
| `audit_log` | B-tree on `(user_id, occurred_at)`, `(resource_type, resource_id)` | Fast audit review by user or by resource |
| `cases` | B-tree on `jurisdiction_id`, partial index on `is_sensitive = true` | Jurisdiction filtering, sensitive-case row-level security enforcement |
| `entity_location_attrs` | GiST index on `geom` (PostGIS) | Geospatial proximity queries |

---

## 5. Authentication & Authorization Flow

1. User authenticates via **Keycloak** (OIDC) with username/password + MFA (OTP or FIDO2 hardware key for supervisory/admin tiers).
2. Keycloak issues a signed JWT containing `user_id`, `role`, `jurisdiction_id`, and `clearance_tier` claims.
3. **API Gateway** validates the JWT on every request and forwards claims to downstream services.
4. Each microservice enforces:
   - **RBAC** — coarse role check (e.g., only `admin` can hit `/admin/*` endpoints).
   - **ABAC** — attribute check against resource: `jurisdiction_id` match, `case_assignments` membership, `is_sensitive` + `clearance_tier` gate.
5. PostgreSQL **Row-Level Security (RLS)** policies enforce jurisdiction- and sensitivity-based filtering at the database layer as a defense-in-depth measure (not solely relying on application-layer checks).
6. Every access decision (allow or deny) on a `case`, `entity`, or `report export` is written to `audit_log` synchronously before the response is returned to the user.
7. Token refresh cycle: 15-minute access tokens, 8-hour refresh tokens, forced re-authentication for sensitive-case access after any idle period > 15 minutes.

---

## 6. Data Ingestion → Normalization → Storage Flow

1. Source connector lands raw file/record in MinIO + emits a Kafka event with `source_id`, `external_ref_id`, `checksum`, `raw_object_key`.
2. Normalization service inserts a row into `source_records` (`ingestion_status = 'processing'`).
3. OCR (if needed) + NER service extracts candidate entities/relationships; writes proposals to a staging table (`entity_extraction_staging`, not shown above for brevity) with per-field confidence scores.
4. Entity Resolution service matches candidates against existing `entities`:
   - Confidence ≥ auto-merge threshold (e.g., 0.92) → auto-inserted/merged, `resolution_status = 'auto_merged'`.
   - Confidence below threshold → queued in an analyst review UI; on confirmation, `resolution_status = 'analyst_confirmed'`.
5. Confirmed entities/relationships are written transactionally to PostgreSQL (`entities`, `relationships`) and asynchronously mirrored to Neo4j via a change-data-capture (CDC) process (Debezium on PostgreSQL WAL → Kafka → Neo4j connector).
6. `source_records.ingestion_status` updated to `'processed'`; malformed records set to `'quarantined'` with an ops-visible reason code.
