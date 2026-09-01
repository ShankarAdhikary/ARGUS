# System Architecture
## ARGUS — AI-Powered Criminal Network Analysis System

## 1. High-Level Technical Architecture Diagram

```
+-----------------------------------------------------------------------------------------------------------------------+
|                                                   USER INTERFACE                                                       |
|                                                                                                                       |
|   +--------------------------+  +--------------------------+  +--------------------------+  +---------------------+   |
|   | Field Investigator Portal|  | Intelligence Analyst Workspace | Supervisor / Executive Dashboard | Admin & Compliance |   |
|   +--------------------------+  +--------------------------+  +--------------------------+  +---------------------+   |
+-----------------------------------------------------------------------------------------------------------------------+
                                                              |
                                                              | HTTPS / WSS / TLS 1.3
                                                              v
+-----------------------------------------------------------------------------------------------------------------------+
|                                              API & GATEWAY LAYER                                                      |
|                                                                                                                       |
|   +---------------------------------------------------------------------------------------------------------------+   |
|   | API Gateway (Kong / Envoy): Rate Limiting, Route Routing, JWT Auth Validation, SSL Termination                |   |
|   +---------------------------------------------------------------------------------------------------------------+   |
|   | Auth & Access Control (Keycloak Integration): OIDC, Multi-Factor Authentication (MFA), ABAC/RBAC Enforcer      |   |
|   +---------------------------------------------------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------------------------------------------------+
                                                              |
                                             +----------------+----------------+
                                             |                                 |
                                             v                                 v
+-------------------------------------------------------------------+  +------------------------------------------------+
|                   CORE ANALYTICAL SERVICES                        |  |             WORKFLOW & AUDIT SERVICES          |
|                                                                   |  |                                                |
|  +---------------------------+  +-------------------------------+ |  |  +-------------------+  +-------------------+  |
|  | Graph Analytics Engine    |  | Pattern & Anomaly Engine      | |  |  | Case Management   |  | Immutable Audit   |  |
|  | (PageRank, Louvain,       |  | (PyG GNN, DBSCAN,             | |  |  | & Notes Service   |  | Service           |  |
|  | Betweenness Centrality)   |  | Isolation Forest)             | |  |  +-------------------+  (Hash-Chained Log)  |
|  +---------------------------+  +-------------------------------+ |  |  +-------------------+  +-------------------+  |
|  +---------------------------+  +-------------------------------+ |  |  | Reporting & Export|  | Alerting & Watch  |  |
|  | Search Service            |  | Entity Resolution Engine      | |  |  | Engine (PDF/DOCX) |  | Engine            |  |
|  | (Elasticsearch API)       |  | (Splink Probabilistic Matcher)| |  |  +-------------------+  +-------------------+  |
|  +---------------------------+  +-------------------------------+ |  +------------------------------------------------+
+-------------------------------------------------------------------+
                                             ^
                                             | Inter-service Event Bus (Kafka Core Stream)
                                             v
+-----------------------------------------------------------------------------------------------------------------------+
|                                          DATA INGESTION & NLP PROCESSING PIPELINE                                     |
|                                                                                                                       |
|  +--------------------+   +-----------------------+   +----------------------+   +---------------------------------+  |
|  | External Connectors|   | Normalization Service |   | OCR & Document Prep  |   | Named Entity Recognition (NER)  |  |
|  | (CCTNS, ICJS, CDR, |-->| (Format Cleaning,     |-->| (Tesseract / Deep    |-->| Engine                          |  |
|  | FIU, Vahan, OSINT) |   | Validation, Hash Check|   | Read, Indic OCR)     |   | (spaCy + IndicBERT Fine-Tuned)  |  |
|  +--------------------+   +-----------------------+   +----------------------+   +---------------------------------+  |
+-----------------------------------------------------------------------------------------------------------------------+
                                                              |
                                                              v
+-----------------------------------------------------------------------------------------------------------------------+
|                                              POLYGLOT PERSISTENCE LAYER                                               |
|                                                                                                                       |
|   +----------------------------+  +----------------------------+  +--------------------------+  +------------------+  |
|   | PostgreSQL 16 + PostGIS    |  | Neo4j Enterprise Cluster   |  | Elasticsearch Cluster    |  | MinIO S3 Object  |  |
|   | (Relational Master Data,   |  | (Graph DB: Entities,       |  | (Full-Text Search Index, |  | Storage          |  |
|   | Users, RLS Case Data)      |  | Relationships, Topologies) |  | Raw Text Search)         |  | (Raw Docs, FIRs) |  |
|   +----------------------------+  +----------------------------+  +--------------------------+  +------------------+  |
+-----------------------------------------------------------------------------------------------------------------------+
```

## 2. Component Descriptions

### 2.1 UI Layer (Web & Mobile Clients)

**Technology:** React 18 (TypeScript), WebGL (Sigma.js graph view), Tailwind CSS.

**Function:** Single-page application providing specialized interfaces tailored to Field Investigators, Intelligence Analysts, Supervisors, and System Administrators. Implements responsive, low-bandwidth interfaces with local caching.

### 2.2 API & Gateway Layer

- **API Gateway (Envoy / Kong):** Manages incoming REST and WebSocket traffic, handles rate limiting, TLS termination, API routing, and payload validation.
- **Auth & Access Control Service (Keycloak Integration):** Validates JSON Web Tokens (JWT) on every request, enforcing fine-grained Role-Based Access Control (RBAC) and Attribute-Based Access Control (ABAC). Enforces Multi-Factor Authentication (MFA) and jurisdiction boundaries.

### 2.3 Data Ingestion & NLP Pipeline

- **External Connectors Layer:** Handles integrations with police systems (CCTNS, ICJS), telecom CDR feeds, financial reporting (FIU), vehicle registries (Vahan), and authorized social/OSINT feeds.
- **Normalization & Validation Service:** Cleans formats, removes duplicates via record checksums, and standardizes data schemas.
- **OCR & Document Prep:** Extracts raw text from scanned PDF/JPEG FIRs and handwritten reports using multi-lingual OCR models.
- **NER Engine:** Extracts entities (People, Organizations, Locations, Vehicles, Phone Numbers, Accounts, Events) using a custom PyTorch/spaCy architecture enhanced with IndicBERT for regional language handling.
- **Entity Resolution Engine:** Uses probabilistic record linkage (Splink/dedupe algorithms) to combine duplicate nodes and establish alias mappings.

### 2.4 Core Analytical Services

- **Graph Analytics Engine:** Executes graph topological algorithms (PageRank, Louvain community detection, Betweenness Centrality) via Neo4j Graph Data Science (GDS) to pinpoint central figures and hidden clusters.
- **Pattern & Anomaly Engine:** Runs background ML jobs (Graph Neural Networks, Isolation Forests, DBSCAN) to discover non-obvious operational signatures, suspicious financial loops, and spatial-temporal co-occurrences.
- **Search Service:** Provides fuzzy, multi-field full-text search across raw documents and extracted metadata powered by an Elasticsearch cluster.

### 2.5 Workflow & Audit Services

- **Case Management Service:** Handles investigator annotations, entity tagging, evidence linking, and access restriction state.
- **Immutable Audit Service:** Writes all actions (read, search, modify, export) to an append-only, hash-chained database log table (PostgreSQL RLS-locked) to guarantee evidentiary tamper-resistance.
- **Reporting & Export Engine:** Compiles interactive workspace maps into formal, court-ready PDF/DOCX documents complete with source citations and confidence metrics.
- **Alerting Engine:** Evaluates incoming data feeds against user-configured watchlist rules and pattern criteria to deliver push and email notifications.

### 2.6 Persistence Layer

- **PostgreSQL 16 (w/ PostGIS):** Master system of record for structured metadata, user identities, permissions, case states, PostGIS spatial data, and audit records.
- **Neo4j Enterprise:** Optimized graph database hosting entity nodes, multi-dimensional relationships, edge weights, and source metadata pointers.
- **Elasticsearch Cluster:** Distributed search index storing processed FIR texts, transcripts, and unstructured notes for high-performance search retrieval.
- **MinIO Storage:** Highly scalable, S3-compatible, encrypted object storage for original case files, scanned FIR documents, and media uploads.

## 3. Detailed Data Flow

### 3.1 Data Ingestion & Enrichment Pipeline

1. **Ingestion Trigger:** A new FIR PDF is pushed to an external connector via a CCTNS web API, or an analyst manually uploads a bulk CDR CSV file via the UI.
2. **Object Persistence & Queueing:** The raw file is stored in MinIO with AES-256 encryption. A message containing `source_id`, file metadata, and checksum is pushed to the Kafka topic `raw.documents`.
3. **Validation & Text Extraction:** The Ingestion Service reads the Kafka message, verifies the checksum to prevent duplicate processing, and sends the document to the OCR Service. Extracted text is written to Kafka topic `processed.text`.
4. **Named Entity Recognition (NER):** The NLP Pipeline processes the text payload, identifying candidate entities (e.g., names, phone numbers, addresses). Each candidate is tagged with an extraction confidence score.
5. **Entity Resolution (Deduplication):** Candidate entities pass to the Entity Resolution Service. Candidates with high similarity (confidence ≥0.92) to existing system entities are merged automatically. Low-confidence matches are flagged for human review in the Analyst Queue.
6. **Dual Store Indexing:**
   - **PostgreSQL:** New entity master records, updated source pointers, and detailed metadata attributes are committed transactionally.
   - **Neo4j:** Relationships and nodes are written/updated graph-side using Cypher transactions initiated by a Kafka CDC worker.
   - **Elasticsearch:** Document contents and extracted entity tags are indexed for full-text queries.

### 3.2 Investigative Query & Visualization Flow

1. **Search Input:** An investigator enters a target phone number into the UI global search bar.
2. **Authentication & Scoping:** The API Gateway verifies the user's JWT, extracting `user_id`, `jurisdiction_id`, and `clearance_tier`.
3. **Search Execution:** The Search Service queries Elasticsearch and PostgreSQL for matching entity profiles within the user's jurisdiction.
4. **Graph Expansion:** Selecting an entity sends a request to the Graph Service. A Cypher query executes against Neo4j, traversing relationships up to N hops within authorized case boundaries.
5. **Audit Generation:** The Immutable Audit Service records an entry containing the user ID, timestamp, target entity, search parameters, and justification string into the write-only PostgreSQL audit table.
6. **UI Rendering:** The API Gateway returns the node-edge JSON payload to the UI, where Sigma.js renders an interactive force-directed graph.

## 4. Infrastructure & Deployment Architecture

```
                                [ TRAFFIC / INGRESS ]
                                          |
                                          v
                +---------------------------------------------------+
                |  Hardware Load Balancer / Firewall (Kube Ingress) |
                +---------------------------------------------------+
                                          |
                   +----------------------+----------------------+
                   |                                             |
                   v                                             v
        [ K8S NODE POOL A: API ]                      [ K8S NODE POOL B: WORKERS ]
+----------------------------------------+    +----------------------------------------+
| - API Gateway Pods                     |    | - Ingestion Worker Pods                |
| - Frontend Web App Pods                |    | - NLP / NER Model Pods (GPU-enabled)   |
| - Core Service Microservices           |    | - Entity Resolution Worker Pods        |
+----------------------------------------+    +----------------------------------------+
                   |                                             |
                   +----------------------+----------------------+
                                          |
                                          v
                             [ STATEFUL STORAGE CLUSTER ]
+--------------------------------------------------------------------------------------+
|  +---------------------+  +---------------------+  +-------------------------------+ |
|  | Neo4j Causal Cluster|  | PostgreSQL HA       |  | Elasticsearch Node Cluster    | |
|  | (1 Core + 2 Replicas|  | (Patroni Hot Standby|  | (3 Node Master/Data Cluster)  | |
|  +---------------------+  +---------------------+  +-------------------------------+ |
+--------------------------------------------------------------------------------------+
```

### 4.1 Topology & Deployment Strategy

**Cloud Architecture:** On-premise hybrid cloud infrastructure hosted within secure government data centers (NIC / MeghRaj cloud) running on a bare-metal Enterprise Kubernetes Cluster.

**Compute Segmentation:**
- **Stateless API Tier:** Kubernetes Pods running microservices (FastAPI/Node) auto-scaled via Horizontal Pod Autoscalers (HPA) based on CPU/memory usage.
- **ML Compute Pool:** Dedicated GPU-accelerated Kubernetes Node Pools (NVIDIA A10G/T4) reserved exclusively for NLP inference, OCR processing, and Graph Neural Network training.

**High Availability & Persistence:**
- **PostgreSQL:** Configured with Patroni and pgBackRest for automated failover, primary-secondary replication, and point-in-time recovery (PITR).
- **Neo4j Enterprise:** Deployed as a Causal Clustering topology consisting of primary Core servers (writing data and managing raft consensus) and Read Replicas (scaling analytical graph query workloads).
- **Kafka:** Deployed as a 3-broker cluster with a minimum in-sync replica (ISR) setting of 2 to ensure zero message loss for ingested crime data.

### 4.2 Disaster Recovery & Business Continuity

- **Target RPO/RTO:** Recovery Point Objective (RPO) <15 minutes; Recovery Time Objective (RTO) <2 hours.
- **Backup Strategy:** Daily encrypted database snapshots and hourly incremental WAL archiving written to an off-site, air-gapped MinIO storage node located in a secondary government data center zone.
