# Product Requirements Document (PRD)
## ARGUS — AI-Powered Criminal Network Analysis System
**Prepared for:** Ministry of Home Affairs — National Crime Records Bureau (NCRB), Women Safety Division
**Problem Statement ID:** 26189

---

## 1. Executive Summary

ARGUS is a data fusion and graph-intelligence platform that ingests fragmented, multi-source crime data (FIRs, CDRs, financial records, surveillance reports, social media intelligence, criminal history databases, and inter-agency intelligence reports), automatically extracts entities and relationships, and surfaces hidden criminal networks to investigators. The system replaces manual cross-referencing — currently slow and error-prone — with AI-assisted entity resolution, relationship mapping, and pattern detection, while preserving the chain-of-custody and auditability required for evidentiary use.

ARGUS is an **investigative aid, not an autonomous decision-maker**. Every insight it produces is scored with a confidence level and traceable back to source documents; final judgment always rests with a human investigator or supervising officer.

---

## 2. Goals and Non-Goals

### 2.1 Goals
- Reduce the time to identify hidden connections between suspects from days/weeks of manual work to minutes/hours.
- Provide a single pane of glass across previously siloed data sources.
- Surface key influencers, suspicious clusters, and anomalous behavioral patterns automatically.
- Maintain full auditability, data provenance, and legal defensibility of every insight.
- Support the specific operational needs of the Women Safety Division (e.g., repeat-offender tracking, organized trafficking ring detection, cyber-harassment network mapping).

### 2.2 Non-Goals
- ARGUS does not perform automated arrests, automated legal determinations, or predictive policing against individuals who have no data footprint in the system (no "pre-crime" scoring of uninvolved citizens).
- ARGUS does not replace forensic evidence systems; it is an analytical layer on top of existing systems of record.
- ARGUS does not release data outside authorized law-enforcement jurisdictional boundaries without explicit inter-agency data-sharing agreements.

---

## 3. User Personas

| Persona | Role Summary | Primary Needs |
|---|---|---|
| **Field Investigator (Sub-Inspector / Inspector)** | Works active cases, needs quick answers about a suspect or FIR | Fast entity search, mobile-friendly case view, ability to flag/annotate leads |
| **Intelligence Analyst** | Works across cases, builds network maps, correlates patterns across districts | Deep graph exploration, pattern detection dashboard, cross-case correlation, report generation |
| **Supervising Officer (DySP/SP and above)** | Oversees multiple cases/districts, approves escalations | Aggregated dashboards, case prioritization view, audit trail visibility, approval workflows |
| **Women Safety Division Specialist** | Focused on trafficking, harassment, repeat-offender, and domestic-violence-linked networks | Specialized pattern templates (e.g., recruitment chains, financial mule networks), victim-safety-aware data handling |
| **System Administrator** | Manages users, data source integrations, security posture | RBAC configuration, data source onboarding, audit log review, system health monitoring |
| **Data Steward / Compliance Officer** | Ensures lawful data use and retention compliance | Data lineage view, retention policy enforcement, access request review |

---

## 4. User Stories

### Investigator
- As an investigator, I want to search for a person by name, phone number, or vehicle number and instantly see all records connecting to them, so I don't have to query five separate systems.
- As an investigator, I want to see a visual map of who a suspect has called, transacted with, or been co-located with, so I can identify unknown associates.
- As an investigator, I want the system to flag when a "new" suspect actually matches an existing criminal history record under a different spelling/alias, so identity fraud doesn't hide a repeat offender.

### Analyst
- As an analyst, I want to run a query across all open cases in a district to detect clusters of individuals repeatedly appearing together, so I can identify an organized network rather than isolated incidents.
- As an analyst, I want the system to rank individuals in a network by centrality/influence score, so I can identify likely ringleaders rather than low-level operatives.
- As an analyst, I want to detect temporal patterns (e.g., recurring travel before incidents in a corridor), so I can predict likely future hotspots for patrol deployment.

### Supervisor
- As a supervising officer, I want a dashboard of all active network investigations with risk scores, so I can prioritize resource allocation.
- As a supervisor, I want to see who accessed which case data and when, so I can ensure investigative integrity and prevent leaks.

### Women Safety Specialist
- As a Women Safety Division analyst, I want to trace financial flows linked to a suspected trafficking recruiter across multiple FIRs and bank records, so I can build a case against the full network rather than a single individual.
- As an analyst, I want social-media-linked harassment reports to be automatically linked to known repeat offenders in the criminal history database, so patterns of targeting are not missed.

---

## 5. Core Features

1. **Multi-Source Data Ingestion** — connectors for FIR databases (CCTNS), CDR feeds, bank/UPI transaction reports, surveillance report uploads, social media intelligence feeds, criminal history databases (ICJS), and inter-agency intelligence (NATGRID-class feeds where authorized).
2. **Entity Extraction (NER)** — automatic extraction of people, organizations, locations, vehicles, phone numbers, financial account identifiers, and events from structured and unstructured (free-text, scanned, regional-language) documents.
3. **Entity Resolution** — deduplication and identity matching across sources (alias detection, fuzzy name matching, phonetic matching for Indian names, ID cross-referencing via Aadhaar-linked systems where legally authorized).
4. **Relationship Mapping Engine** — builds a knowledge graph of direct (explicit call/transaction/co-accused) and indirect (shared location, shared associate, temporal co-occurrence) relationships.
5. **Network Visualization** — interactive graph explorer with filtering, timeline scrubbing, and expand/collapse of entity clusters.
6. **Influencer / Key-Player Identification** — graph centrality analytics (degree, betweenness, eigenvector centrality, PageRank variants) to rank likely organizers vs. peripheral actors.
7. **Pattern & Anomaly Detection** — unsupervised clustering, temporal sequence analysis, and rule-based heuristics to flag suspicious clusters, unusual financial flows, and repeat-offender recurrence patterns.
8. **Case Workspace** — a per-case (or per-network) workspace where investigators can pin entities, add notes, tag leads, and build an evidence narrative.
9. **Report Generation & Export** — generate formatted investigative reports (PDF/DOCX) with network diagrams, confidence-annotated findings, and citation back to source records, exportable for judicial submission.
10. **Confidence Scoring** — every AI-generated relationship or pattern carries a confidence score and the underlying evidence trail, never presented as a bare assertion.
11. **Role-Based Access Control & Audit Trail** — every data access, query, and export is logged; access is scoped by case, jurisdiction, and clearance level.
12. **Alerting** — configurable alerts when a new record links to a person/entity already under investigation or on a watchlist.

---

## 6. Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | System shall ingest data from at least 7 defined source types via batch upload and API/streaming connectors. |
| FR-02 | System shall extract named entities (person, org, location, vehicle, phone, financial account, event) with a minimum extraction F1-score target of 0.85 on validation datasets, in English and at least 3 major regional languages at MVP (Hindi, plus 2 others configurable per state). |
| FR-03 | System shall resolve entity duplicates/aliases with a human-in-the-loop confirmation step before merging identities. |
| FR-04 | System shall construct and persist a graph representation of entities and relationships, updated incrementally as new data arrives. |
| FR-05 | System shall allow investigators to search by any entity attribute and retrieve a ranked, explorable network view within 3 seconds for graphs up to 10,000 nodes. |
| FR-06 | System shall compute and display centrality/influence rankings for any selected sub-network on demand. |
| FR-07 | System shall run scheduled and on-demand pattern-detection jobs and present flagged clusters/anomalies with supporting evidence links. |
| FR-08 | System shall allow annotation, tagging, and case-linking of entities and relationships by authorized users. |
| FR-09 | System shall generate exportable investigative reports including graph snapshots, source citations, and confidence scores. |
| FR-10 | System shall enforce RBAC such that users only see data within their jurisdiction/case assignment unless explicitly elevated. |
| FR-11 | System shall log every read/write/export action with user ID, timestamp, and justification (for sensitive queries) into an immutable audit log. |
| FR-12 | System shall support configurable alerting rules (e.g., "notify when entity X appears in a new record"). |

---

## 7. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Security** | AES-256 encryption at rest, TLS 1.3 in transit, MFA for all users, hardware-token option for supervisory roles. |
| **Availability** | 99.5% uptime for production; degraded-mode read access during maintenance windows. |
| **Scalability** | Support ingestion of 5M+ documents/records per state per year and graphs exceeding 50M nodes/edges nationally, horizontally scalable. |
| **Performance** | Entity search response < 3s (p95); graph render for sub-networks up to 1,000 nodes < 5s. |
| **Auditability** | All actions immutable-logged; audit log retained per NCRB data retention policy (minimum 10 years or as mandated). |
| **Data Residency** | All data hosted within India, on MeghRaj/empanelled government cloud or on-premise government data centers; no data leaves national jurisdiction. |
| **Compliance** | Alignment with IT Act 2000, Digital Personal Data Protection (DPDP) Act 2023 (law-enforcement exemptions applied per statute), CrPC/BNSS evidentiary standards, CERT-In empanelment guidelines, STQC certification for the platform. |
| **Accessibility** | UI compliant with WCAG 2.1 AA; supports low-bandwidth field connections (progressive loading, offline-cache for mobile). |
| **Localization** | UI and NLP pipeline support English + Hindi at launch, extensible to other 22nd Schedule languages. |
| **Interoperability** | Standards-based APIs (REST/GraphQL) for integration with CCTNS, ICJS, and state police IT systems. |

---

## 8. Constraints Specific to Law Enforcement Use

- **Chain of custody**: Any data used as evidence must retain an unbroken, logged lineage from source system to report output.
- **Legal admissibility**: AI-derived insights are investigative leads, not evidence in themselves; UI must clearly label AI-generated content as "investigative lead — verify before use" and separate it from verified source facts.
- **Jurisdictional boundaries**: Cross-state/cross-agency data sharing requires explicit MOU-backed access grants configured by administrators, not implicit system-wide visibility.
- **Sensitive-case handling**: Cases involving minors, sexual assault survivors, or witness protection must have elevated access restrictions (need-to-know, restricted export) irrespective of the requesting user's general clearance.
- **Bias and fairness**: Entity resolution and pattern detection models must be periodically audited for demographic bias (name/ethnicity/region false-positive skew) given India's linguistic and cultural diversity.
- **No fully automated adverse action**: The system never auto-generates arrest recommendations, watchlist additions, or public-facing outputs without human review and sign-off.

---

## 9. Success Metrics

| Metric | Target (Year 1 post-rollout) |
|---|---|
| Average time to map a suspect's network (baseline: manual, ~3–5 days) | < 4 hours |
| Entity extraction accuracy (F1) | ≥ 0.85, improving to ≥ 0.90 by Year 2 |
| % of flagged patterns rated "useful lead" by investigators (feedback loop) | ≥ 60% |
| Cross-source case linkages discovered that were previously missed manually | Track and report quarterly; target ≥ 25% increase in linked-case discovery |
| System uptime | ≥ 99.5% |
| Audit log completeness (actions logged vs. actions performed) | 100% |
| User adoption (active weekly users among trained investigators) | ≥ 70% within 6 months of rollout |

---

## 10. Rollout Considerations

- **Pilot phase**: Deploy in 1–2 states/districts with high case volume relevant to Women Safety Division (e.g., trafficking corridors) before national rollout.
- **Training**: Mandatory certification program for investigators and analysts before production access is granted.
- **Change management**: Parallel-run period where ARGUS outputs are cross-checked against existing manual processes before full reliance.
- **Feedback loop**: Built-in mechanism for investigators to rate the usefulness of AI-generated leads, feeding back into model retraining (see TRD, Section 6).
