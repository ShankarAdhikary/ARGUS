# Implementation Plan
## ARGUS — AI-Powered Criminal Network Analysis System

## 1. Phased Build Roadmap

The development of ARGUS is organized into five sequential phases over an 18-month timeline to enable continuous feature validation, early operational deployment, and progressive scaling.

```
+----------------------------------------------------------------------------------------------------+
|                                    18-MONTH IMPLEMENTATION TIMELINE                                 |
+----------------------------------------------------------------------------------------------------+
| Phase 1: Foundation & Data Ingestion Pipeline (Months 1 - 4)                                       |
| [====================]                                                                             |
|                                                                                                    |
| Phase 2: Core NLP, Entity Resolution & Graph Storage (Months 4 - 8)                                |
|                      [====================]                                                        |
|                                                                                                    |
| Phase 3: Visual Network Explorer & Investigator Workspace (Months 8 - 11)                           |
|                                           [=================]                                      |
|                                                                                                    |
| Phase 4: Pattern Detection, Analytics & Alerting (Months 11 - 14)                                  |
|                                                             [=================]                    |
|                                                                                                    |
| Phase 5: Security Hardening, Audit Lock down & Pilot Deployment (Months 14 - 18)                   |
|                                                                               [====================|
+----------------------------------------------------------------------------------------------------+
```

## 2. Phase Breakdown and Deliverables

### Phase 1: Foundation & Data Ingestion Pipeline (Months 1–4)

**Objectives:** Establish core infrastructure, set up persistent stores, build secure integration pipelines, and ingest unstructured/structured sample records.

**Deliverables:**
- Deployment of Kubernetes cluster infrastructure, HashiCorp Vault, Keycloak IAM, PostgreSQL master database, MinIO object storage, and Kafka cluster.
- Implementation of secure API Ingestion Gateway and integration connectors for CCTNS FIR data, structured CSV feeds (CDR records), and banking spreadsheets.
- Normalization pipeline ensuring record deduplication using cryptographic payload checksums.
- Integration of Tesseract/Indic OCR pipeline for extracting raw text from scanned FIR documents.

**Milestone 1:** Successful ingest, normalization, and raw text storage of 100,000 historical FIRs and CDR logs into the staging environment.

### Phase 2: Core NLP, Entity Resolution & Graph Storage (Months 4–8)

**Objectives:** Build entity extraction engines, configure Neo4j graph schemas, implement entity resolution logic, and construct initial graph topologies.

**Deliverables:**
- Deployment of spaCy/IndicBERT fine-tuned Named Entity Recognition (NER) pipeline for Indian legal contexts (English and Hindi).
- Neo4j graph cluster setup, defining schemas, indexes, and primary node/edge properties.
- Implementation of the Splink-based Entity Resolution module, enabling fuzzy identity matching, alias clustering, and human-in-the-loop review queues.
- Automated CDC pipeline streaming resolved entities and relationships from PostgreSQL directly to Neo4j.

**Milestone 2:** Processing ingested records into a populated Neo4j knowledge graph with auto-resolved duplicate profiles and baseline identity confidence scoring.

### Phase 3: Visual Network Explorer & Investigator Workspace (Months 8–11)

**Objectives:** Develop the frontend interface, build interactive graph exploration views, enable full-text entity search, and deploy basic case management tools.

**Deliverables:**
- Release of the React web UI featuring high-performance canvas graph rendering (Sigma.js) capable of visualizing multi-hop sub-networks.
- Multi-attribute entity search powered by Elasticsearch with support for dynamic filtering (confidence level, date range, entity type).
- Implementation of the Case Workspace module: entity pinning, notes addition, evidence linking, and access logging.
- Development of the PDF/DOCX Report Generation Engine featuring source citations and graph snapshots.

**Milestone 3:** End-to-end user acceptance of the visual network search, graph exploration, and report creation workflows by pilot investigative teams.

### Phase 4: Pattern Detection, Analytics & Alerting (Months 11–14)

**Objectives:** Embed graph algorithms, build ML anomaly detection engines, deploy automated alert systems, and introduce Women Safety specific analytical workflows.

**Deliverables:**
- Integration of Neo4j Graph Data Science (GDS) algorithms (PageRank, Louvain, Betweenness Centrality) to automatically surface network influencers.
- Deployment of pattern-detection engines (Isolation Forests, temporal DBSCAN) for identifying financial mule networks, recurring co-locations, and trafficking recruitment chains.
- Real-time alerting engine dispatching notifications when targeted entities or watched patterns appear in incoming data streams.
- Analyst feedback mechanism (Confirm / Dismiss lead) integrated to capture model reinforcement data.

**Milestone 4:** Automated discovery of complex multi-entity criminal patterns and key influencer nodes with quantifiable predictive feedback metrics.

### Phase 5: Hardening, Compliance & Pilot Deployment (Months 14–18)

**Objectives:** Execute security auditing, complete STQC/CERT-In compliance testing, implement strict audit log controls, and deploy to pilot districts.

**Deliverables:**
- Implementation of PostgreSQL append-only, hash-chained audit logging to guarantee legal defensibility.
- Execution of CERT-In empanelled penetration testing and remediation of identified vulnerabilities.
- Deployment of PostgreSQL Row-Level Security (RLS) policies for strict jurisdictional and sensitive case isolation.
- Pilot deployment within designated Women Safety Division investigative units, accompanied by training and operational handover.

**Milestone 5:** Full operational STQC certification and deployment of ARGUS in pilot state jurisdictions.

## 3. Critical Path & Dependencies

```
[Phase 1: Infra Setup & Data Connectors]
                   |
                   v
[Phase 2A: NLP Model Training] -------> [Phase 2B: Entity Resolution Engine]
                                                        |
                                                        v
                                        [Phase 2C: Graph Ingestion Pipeline]
                                                        |
                                                        v
                                        [Phase 3A: Network Visualizer & UI]
                                                        |
                                                        v
                                        [Phase 4A: GDS & Pattern Analytics]
                                                        |
                                                        v
                                        [Phase 5: Audit Hardening & Certs]
```

### Critical Path Items

- **CCTNS & CDR Data Connector Approval (Phase 1):** Any delay in obtaining test schemas or data access permissions from state/national crime databases halts downstream NLP model fine-tuning.
- **Domain-Specific NER Accuracy (Phase 2A):** Achieving ≥0.85 F1 score in entity extraction across English and regional languages is a strict blocker for high-quality graph building.
- **Graph Engine Sync Performance (Phase 2C):** Reliable streaming synchronization between PostgreSQL metadata and Neo4j topology is critical to prevent state drift.
- **STQC / CERT-In Security Certification (Phase 5):** Security approval is an indispensable prerequisite for connecting ARGUS to live law-enforcement network infrastructure.

## 4. Effort Estimation Summary

| Phase | Duration | Core Engineering Roles Required | Estimated Total Person-Months |
|---|---|---|---|
| Phase 1: Foundation & Ingestion | 4 Months | Data Engineers (3), DevOps Engineers (2), Backend Developers (2) | 28 Person-Months |
| Phase 2: NLP & Graph Engineering | 4 Months | ML/NLP Engineers (3), Graph DB Specialists (2), Backend Developers (2) | 28 Person-Months |
| Phase 3: Visual UI & Workspace | 3 Months | Frontend Engineers (3), UI/UX Designers (1), Backend Developers (2) | 18 Person-Months |
| Phase 4: Pattern Analytics & Alerts | 3 Months | Data Scientists / ML Engineers (2), Graph Engineers (2), Backend Developers (1) | 15 Person-Months |
| Phase 5: Security & Pilot Deployment | 4 Months | Security Engineers (2), QA Engineers (2), DevOps (1), Field Trainers (2) | 28 Person-Months |
| **TOTAL** | **18 Months** | **Cross-functional Engineering Team** | **117 Person-Months** |

## 5. Early Investigative Value Wins

To deliver immediate operational utility before full system completion, the build sequence prioritizes early analytical outputs:

- **Month 4 (End of Phase 1): Unified Entity Search Utility** — Investigators receive a lightweight search interface to query across raw text documents, CDR logs, and FIRs simultaneously, eliminating manual cross-referencing across separate databases.
- **Month 8 (End of Phase 2): Automated Suspect Profiler** — Investigators can input a single suspect's profile to extract a consolidated dossier detailing all recorded aliases, registered phone numbers, associated vehicle plates, and known co-accused individuals.
- **Month 11 (End of Phase 3): Interactive Visual Case Board** — Analysts gain access to drag-and-drop graph visualizations and one-click PDF report generation to produce court-ready network diagrams for ongoing cases.
