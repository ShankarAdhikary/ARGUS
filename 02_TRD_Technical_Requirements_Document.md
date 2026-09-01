# Technical Requirements Document (TRD)
## ARGUS — AI-Powered Criminal Network Analysis System

---

## 1. Purpose

This document specifies the technology blueprint for ARGUS: the stack, integrations, data pipelines, security/compliance controls, and scalability parameters required to satisfy the PRD. It is intended to be directly actionable by the engineering team.

---

## 2. Technology Stack

### 2.1 Core Languages
- **Python 3.12** — data pipelines, ML/NLP services, backend microservices (FastAPI)
- **TypeScript** — frontend (React) and Node-based BFF (backend-for-frontend) layer where needed
- **SQL** (PostgreSQL dialect) — relational data and metadata
- **Cypher** — graph queries (Neo4j)

### 2.2 Backend Frameworks
- **FastAPI** — REST/GraphQL API services (async, high throughput, auto-generated OpenAPI specs for auditability)
- **Apache Kafka** — event streaming backbone for ingestion pipeline (decouples source connectors from processing)
- **Apache Airflow** — orchestration of batch ETL and scheduled pattern-detection jobs
- **Celery + Redis** — async task queue for on-demand jobs (e.g., "run pattern detection on this case now")

### 2.3 Databases
| Store | Technology | Purpose |
|---|---|---|
| Graph DB | **Neo4j Enterprise** (causal clustering) — alt. JanusGraph on Cassandra for extreme scale | Entity/relationship graph, centrality & path queries |
| Relational DB | **PostgreSQL 16** (with PostGIS extension) | Case metadata, user/RBAC data, audit logs, structured source records, geospatial queries |
| Search Index | **Elasticsearch / OpenSearch** | Full-text search across FIRs, reports, unstructured documents |
| Object Storage | **MinIO** (S3-compatible, on-prem) | Raw document storage (scanned FIRs, surveillance media), encrypted at rest |
| Cache | **Redis** | Session cache, hot-query cache, rate limiting |
| Time-series (optional, Phase 3) | **TimescaleDB** | CDR/temporal pattern analysis at scale |

### 2.4 ML / NLP Stack
- **spaCy** + **HuggingFace Transformers** — base NER pipeline
- **IndicNLP Suite / AI4Bharat models (IndicBERT, IndicNER)** — regional-language entity extraction (Hindi and other Schedule VIII languages)
- **Custom fine-tuned NER model** — domain-specific entities (FIR-specific phrasing, vehicle registration formats, financial account patterns) trained on annotated law-enforcement corpora
- **PyTorch Geometric (PyG)** — Graph Neural Networks for link prediction and community detection
- **scikit-learn** — classical anomaly detection (Isolation Forest, DBSCAN clustering) for transaction/behavior anomalies
- **NetworkX** (analysis prototyping) / native Neo4j Graph Data Science (GDS) library (production centrality/community algorithms: PageRank, Louvain, Betweenness Centrality)
- **spaCy + regex hybrid** — phone number, vehicle registration, and financial account pattern extraction (structured entity types benefit from rule augmentation over pure ML)
- **dedupe / Splink** — probabilistic record linkage and entity resolution (fuzzy + phonetic matching, tuned for Indian name variants)
- **OCR**: Tesseract / Cloud-agnostic OCR engine for scanned FIRs and handwritten report digitization (with human verification step)

### 2.5 Frontend
- **React 18 + TypeScript**
- **Sigma.js / Cytoscape.js** — large-graph interactive visualization (WebGL-accelerated for graphs with thousands of nodes)
- **D3.js** — custom charts (temporal pattern timelines, centrality distributions)
- **Tailwind CSS** — design system implementation
- **React Query** — data fetching/caching

### 2.6 Infrastructure & DevOps
- **Kubernetes** (on-prem via MeghRaj / NIC data centers, or empanelled government cloud) — container orchestration
- **Docker** — containerization
- **Helm** — deployment templating
- **HashiCorp Vault** — secrets management, encryption key management
- **Keycloak** — identity, SSO, MFA, RBAC token issuance (OAuth2/OIDC)
- **Prometheus + Grafana** — monitoring/alerting
- **ELK/OpenSearch stack** (separate from data-search cluster) — centralized log aggregation for ops (distinct from the immutable audit log store)
- **HashiCorp Consul / Istio** (optional) — service mesh for internal mTLS between microservices

---

## 3. Required APIs and Third-Party / Government System Integrations

| Integration | Purpose | Notes |
|---|---|---|
| **CCTNS** (Crime and Criminal Tracking Network & Systems) | FIR and police station record ingestion | Primary FIR source; requires MHA data-sharing authorization |
| **ICJS** (Inter-operable Criminal Justice System) | Cross-reference with courts, prisons, forensics, prosecution data | Read-only integration |
| **Telecom CDR feeds** (via lawful interception/authorized telecom liaison channels) | Call detail record ingestion | Requires warrant-backed access per telecom law; ingestion only for authorized case numbers |
| **Bank/UPI/NPCI transaction reports** | Financial pattern analysis | Ingested via FIU-IND / bank nodal officer channels, not direct bank API access |
| **State Police Surveillance Report Systems** | Field surveillance uploads | Custom connector per state system, or manual upload with OCR/NLP extraction |
| **Social Media Intelligence** | OSINT signals relevant to active cases | Ingested only for platform-compliant, legally authorized monitoring (e.g., public posts flagged in a case), not blanket scraping |
| **NATGRID-class intelligence feeds** | Cross-agency intelligence correlation | Access gated by national security clearance tier; separate compliance review |
| **Aadhaar-linked identity verification** (via UIDAI-authorized channels only) | Identity resolution assistance | Used strictly per Aadhaar Act provisions for law-enforcement identity verification; not stored raw, only match/no-match tokens |
| **Vahan/Sarathi (vehicle & driving license registry)** | Vehicle entity resolution | Read-only API integration |

All external integrations are mediated through a dedicated **Integration Gateway** service (Section 5) so that source systems are never directly exposed to the analytics layer, and every external call is logged.

---

## 4. Data Processing Pipeline

```
[Source Systems] → [Ingestion Connectors] → [Kafka Topics: raw.<source>]
        → [Validation & Format Normalization Service]
        → [Kafka Topics: normalized.<source>]
        → [OCR (if scanned) → Text Extraction]
        → [NER / Entity Extraction Service]
        → [Entity Resolution Service (dedupe/merge with human-in-loop queue)]
        → [Graph Ingestion Service] → [Neo4j Graph DB]
              ↘ [PostgreSQL: structured metadata + audit trail]
              ↘ [Elasticsearch: full-text index]
        → [Pattern Detection Jobs (Airflow-scheduled + on-demand Celery)]
        → [Alerting Service] → [Notification (in-app / SMS-gateway for field officers)]
```

**Key pipeline properties:**
- **Idempotent ingestion**: every record carries a source-system unique ID + checksum to prevent duplicate ingestion on re-upload.
- **Human-in-the-loop checkpoints**: entity resolution merges above a confidence threshold auto-apply; merges below threshold queue for analyst confirmation.
- **Incremental graph updates**: new records trigger incremental graph writes rather than full re-computation; centrality/community metrics recomputed on a rolling schedule (e.g., nightly) plus on-demand for active cases.
- **Data quality gate**: malformed/unparseable records are routed to a quarantine queue with an ops dashboard, not silently dropped.

---

## 5. System Component List (mapped to Architecture doc)

1. Data Ingestion Layer (source connectors + Integration Gateway)
2. Validation & Normalization Service
3. OCR & Document Processing Service
4. Entity Extraction (NER) Service
5. Entity Resolution Service
6. Relationship/Graph Ingestion Service
7. Graph Analytics Engine (centrality, community detection, link prediction)
8. Pattern Detection & Anomaly Engine
9. Search Service (Elasticsearch-backed)
10. Case & Workflow Management Service
11. Reporting/Export Service
12. Alerting & Notification Service
13. Auth & RBAC Service (Keycloak-backed)
14. Audit Logging Service (append-only)
15. API Gateway
16. Frontend Application

*(Full data-flow diagram in System Architecture document.)*

---

## 6. Security and Compliance Requirements

| Control | Specification |
|---|---|
| Encryption at rest | AES-256 for all databases and object storage; keys managed via HashiCorp Vault with periodic rotation |
| Encryption in transit | TLS 1.3 for all external and internal service-to-service traffic (mTLS via service mesh) |
| Authentication | MFA mandatory for all users; hardware token (FIDO2) required for supervisory/admin roles |
| Authorization | Fine-grained RBAC + ABAC: role, jurisdiction, case assignment, and data-sensitivity tier all factor into access decisions |
| Audit logging | Immutable, append-only audit log (write-once storage) capturing user, action, timestamp, data accessed, and justification for sensitive queries; log itself is tamper-evident (hash-chained) |
| Data retention | Configurable per NCRB policy; default case data retention aligned to CrPC/BNSS evidentiary timelines; audit logs retained minimum 10 years |
| Data residency | All infrastructure within Indian sovereign cloud (MeghRaj empanelled) or on-premise NIC/state data centers; no cross-border data transfer |
| Data minimization | Only fields necessary for investigative purpose are extracted/stored from source systems; raw sensitive identifiers (e.g., full Aadhaar number) are tokenized, never stored in plaintext in the analytics layer |
| Penetration testing | Mandatory CERT-In empanelled auditor pentest before go-live and annually thereafter |
| Certification | STQC certification target before production rollout |
| Model governance | ML models version-controlled, bias-audited quarterly, and retrained only through a documented, logged pipeline (no silent model updates in production) |
| Incident response | Documented breach-notification workflow aligned with CERT-In reporting timelines and DPDP Act requirements |
| Sensitive case isolation | Cases flagged "sensitive" (minors, sexual assault, witness protection) enforce need-to-know access at the database row level (PostgreSQL row-level security + graph-level sub-graph access control) |

---

## 7. Scalability Parameters

| Dimension | MVP (Pilot) | Year 1 (State-wide) | Year 3 (National) |
|---|---|---|---|
| Ingested records/day | 50,000 | 500,000 | 5,000,000+ |
| Graph size (nodes) | 500,000 | 10,000,000 | 100,000,000+ |
| Concurrent users | 100 | 2,000 | 20,000+ |
| Query latency (p95, entity search) | < 3s | < 3s | < 5s (at scale, with caching tiers) |
| Storage growth | ~2 TB/year | ~20 TB/year | ~200 TB/year |

**Scaling approach:**
- Kafka partitioning by source-type and district to parallelize ingestion.
- Neo4j causal clustering (read replicas) for horizontal read scaling; graph sharding by state/jurisdiction considered at national scale (Year 3+) with a federated query layer for cross-jurisdiction investigations.
- Kubernetes Horizontal Pod Autoscaling on NER/entity-resolution services (CPU/GPU-bound, bursty workloads).
- Elasticsearch index lifecycle management (hot-warm-cold tiers) to control storage cost as historical data ages.

---

## 8. Environments

- **Dev** → **Staging (with synthetic/anonymized data only)** → **UAT (with sanitized real data under strict access control)** → **Production**
- No production law-enforcement data is ever used in Dev/Staging environments; synthetic data generation pipeline is a required deliverable before UAT.
