# App Flow / Navigation Logic
## ARGUS — AI-Powered Criminal Network Analysis System

---

## 1. Top-Level Navigation Map

```
Login (MFA)
  └── Role-based Landing
        ├── Investigator Home
        ├── Analyst Home
        ├── Supervisor Dashboard
        └── Admin Console

Global Nav Bar (persistent):
  [Dashboard] [Search] [Network Explorer] [Cases] [Reports] [Alerts] [Admin*] [Profile/Audit]
  (*Admin visible only to admin role)
```

---

## 2. Screen-by-Screen Flow

### 2.1 Login & Authentication
- **Screen: Login** → credentials + MFA (OTP/hardware token) → **Role resolution** (via Keycloak claims: investigator / analyst / supervisor / admin / compliance).
- Failed MFA 3x → account lock + automatic audit flag + notification to admin.
- Successful login → landing screen determined by primary role; users with multiple roles get a role-switcher in the top nav.

### 2.2 Home / Dashboard (role-adaptive)
- **Investigator Home**: "My Cases" list, recent search history, pending alerts on watched entities, quick-search bar.
- **Analyst Home**: Active network investigations, pattern-detection job queue/results feed, cross-case correlation suggestions.
- **Supervisor Dashboard**: District/state-level heat map of active investigations, risk-ranked case list, audit summary widget, resource allocation view.
- **Decision point**: user selects an entity, a case, or a detected pattern → routes into Entity Detail, Case Workspace, or Pattern Detail respectively.

### 2.3 Entity Search
- **Screen: Search** — search bar with type filters (Person / Organization / Location / Vehicle / Phone / Financial Account / Event) + advanced filter panel (date range, district, source type, case linkage).
- User enters query → **Search Results List** (ranked by relevance + confidence) showing entity cards with key attributes and source count.
- **Decision point**:
  - Click entity → **Entity Detail Screen**
  - Click "Explore Network" on any result → **Network Visualization Screen** centered on that entity
  - No results → system suggests fuzzy/phonetic matches ("Did you mean...") drawing on entity resolution service

### 2.4 Entity Detail Screen
- Header: entity name/identifier, entity type, confidence-weighted identity summary (if multiple aliases resolved into one profile, shows merge history).
- Tabs:
  - **Overview** — key attributes, source records list (with citation links back to original FIR/CDR/etc.)
  - **Relationships** — list view of direct connections (person, org, phone, etc.) with relationship type and strength
  - **Timeline** — chronological event history involving this entity
  - **Cases** — linked case files
  - **Notes/Annotations** — investigator-added notes (role-gated: only case-assigned users can edit)
- Actions: [Add to Case] [Add to Watchlist] [Explore in Network View] [Flag for Review] [Export Entity Profile]

### 2.5 Network Visualization (Graph Explorer)
- Central canvas: interactive force-directed graph (Sigma.js), entity nodes color-coded by type, edge thickness reflecting relationship strength/confidence.
- Left panel: filters (relationship type, date range, confidence threshold, source type, "show indirect connections" toggle).
- Right panel: selected-node detail (mirrors Entity Detail summary) + centrality/influence score if computed.
- Top toolbar: [Expand Node] [Collapse Cluster] [Run Pattern Detection on Sub-graph] [Show Shortest Path Between Two Entities] [Timeline Scrubber] [Save View to Case] [Export Graph Image/Data]
- **Decision points**:
  - Double-click node → expand its direct connections
  - Select two nodes → "Find Path" → highlights shortest/most-relevant connection chain with the evidence supporting each hop
  - Right-click node → context menu (Add to Case, Watchlist, Flag, View Full Profile)
  - Apply confidence-threshold filter → graph re-renders showing only relationships above the chosen certainty level (with a visible warning if this hides low-confidence but potentially relevant links)

### 2.6 Pattern Detection Results
- **Screen: Patterns** — list of detected clusters/anomalies (scheduled + on-demand), each with: pattern type (e.g., "suspicious financial cluster," "recurring co-location," "recruitment chain signature"), confidence score, entities involved, and date detected.
- Click a pattern → **Pattern Detail Screen**: explanation of why it was flagged (feature contributions, e.g., "5 transactions >₹50,000 between these 4 accounts within 72 hours, matching known mule-network signature"), supporting sub-graph view, and source citations.
- Actions: [Confirm as Useful Lead] [Dismiss as False Positive] [Escalate to Supervisor] [Add to Case]
- Feedback (Confirm/Dismiss) feeds the model-retraining loop (logged, not immediately applied to production model — see TRD §6).

### 2.7 Case Workspace
- **Screen: Case Detail** — case metadata (FIR number, jurisdiction, assigned officers, status), pinned entities, network snapshot, timeline of investigative actions, attached reports/notes.
- Sub-navigation: [Overview] [Network Map] [Entities] [Evidence/Documents] [Notes] [Report Builder] [Access Log]
- **Decision point**: "Generate Report" → routes to Report Builder flow.

### 2.8 Report Builder / Export
- Step 1: Select scope (whole case / selected entities / selected sub-network).
- Step 2: Select sections to include (network diagram, entity profiles, timeline, pattern findings, source citations).
- Step 3: Preview — every AI-derived claim visibly tagged with a confidence badge and source citation.
- Step 4: Export as PDF/DOCX → export action is logged with justification field (mandatory for cases marked sensitive) → download link delivered.

### 2.9 Alerts
- **Screen: Alerts** — list of triggered alerts (e.g., "Watched entity appeared in new FIR", "New pattern match to open case").
- Click alert → routes to relevant Entity Detail / Pattern Detail / Case screen.
- Configure alert rules (Investigator/Analyst self-service, within permitted scope): entity-based watch, pattern-type subscriptions.

### 2.10 Admin Console (Admin role only)
- **User Management**: create/deactivate users, assign roles/jurisdictions.
- **Data Source Management**: configure/monitor ingestion connectors, view ingestion health, manage quarantine queue.
- **Audit Log Viewer**: searchable, filterable, exportable (for compliance/oversight bodies).
- **Model Management**: view active model versions, review retraining proposals, approve/reject model updates (compliance officer co-sign required per TRD §6 model governance).

---

## 3. Key Cross-Cutting Interaction Flows

### 3.1 Uploading New Data
`Case Workspace → Evidence/Documents tab → [Upload] → file type detection → (if scanned) OCR queue → NLP extraction preview shown to user → user confirms/edits extracted entities → entities merged into graph → confirmation toast + audit log entry`

### 3.2 Querying a Network
`Search → select entity → Explore Network → apply filters → (optional) Run Pattern Detection on Sub-graph → review flagged patterns → pin relevant entities to case`

### 3.3 Filtering Results
All list and graph views share a common filter component (confidence threshold, date range, source type, jurisdiction) so behavior is consistent across Search, Network Explorer, and Pattern Detection screens.

### 3.4 Generating and Exporting Reports
`Case Workspace → Report Builder → scope/section selection → preview with citations → export → audit-logged download`

### 3.5 Sensitive Case Access
Any navigation into a case flagged "sensitive" triggers an additional access-justification prompt (logged) before the Case Workspace loads, regardless of the user's general role permissions.

---

## 4. Error & Edge-Case Flows

- **No network connectivity (field/mobile)**: cached last-synced entity/case data viewable read-only; actions queue locally and sync when connection restores; user is clearly shown "offline mode" state.
- **Low-confidence entity resolution**: system never silently auto-merges two identities below the configured confidence threshold — routes to an analyst confirmation queue instead.
- **Conflicting source data** (e.g., two different DOBs for the same person across sources): Entity Detail screen surfaces the conflict explicitly rather than picking one silently, with source attribution for each value.
- **Unauthorized access attempt** (user navigates to a case/entity outside their jurisdiction): access denied screen + automatic audit flag; repeated attempts trigger supervisor notification.
