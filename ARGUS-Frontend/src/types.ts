// Shared types for the ARGUS console. These mirror the live backend API
// contract documented in WORK_PLAN_24H.md.

export type Role = "investigator" | "analyst" | "supervisor" | "admin";

export interface Session {
  token: string;
  user: AuthUser;
}

export interface AuthUser {
  user_id: string;
  employee_id: string;
  full_name: string;
  role: Role;
  jurisdiction: string;
}

export type EntityType =
  | "person"
  | "phone"
  | "vehicle"
  | "location"
  | "financial_account"
  | "organization"
  | "event"
  | "fir";

export interface CaseSummary {
  case_id: string;
  title: string;
  fir_number: string;
  jurisdiction: string;
  status: "open" | "under_review" | "closed";
  is_sensitive: boolean;
  sensitivity_reason?: string;
  category?: string;
  opened_at: string;
}

export interface CaseEntityLink {
  entity_type: EntityType;
  entity_value: string;
  linked_by: string;
  linked_at: string;
}

export interface CaseNote {
  note_id: string;
  author: string;
  content: string;
  created_at: string;
}

export interface CaseDetail extends CaseSummary {
  entities: CaseEntityLink[];
  notes: CaseNote[];
}

export type ConfidenceTier = "high" | "medium" | "low";

export interface PatternRecord {
  pattern_id: string;
  pattern_type: string;
  confidence: number;
  description: string;
  explanation: string;
  entities: string[];
  detected_at: string;
  status: "new" | "confirmed" | "dismissed" | "escalated";
  source: "burner_heuristic" | "centrality" | "co_location" | "financial_cluster" | "graph_recurrence" | "co_accused";
  /** True only when a meaningful share of the linked FIRs are trafficking / exploitation-of-persons cases. */
  women_safety_flag?: boolean;
  women_safety_fir_count?: number;
  risk_tier?: "HIGH" | "MEDIUM" | "LOW";
}

export interface CentralityRow {
  entity_value: string;
  entity_type: EntityType;
  pagerank: number;
  betweenness: number;
}

export interface AlertRule {
  rule_id: string;
  entity_value: string;
  created_at: string;
}

export interface AlertRecord {
  alert_id: string;
  rule_id: string;
  entity_value: string;
  message: string;
  triggered_at: string;
  read_status: boolean;
}

export interface AuditEntry {
  audit_id: string;
  user: string;
  role: Role;
  action: string;
  resource: string;
  justification?: string;
  occurred_at: string;
}

export interface IngestionHealth {
  status: "healthy" | "degraded";
  lastRun: string | null;
  queued: number;
  processing: number;
  processed: number;
  quarantined: number;
}

export interface GraphNode {
  data: {
    id: string;
    label: string;
    type: EntityType;
    confidence?: number;
  };
}

export interface GraphEdge {
  data: {
    id: string;
    source: string;
    target: string;
    label?: string;
    direct: boolean;
  };
}

export interface GraphElements {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type ApiRecord = Record<string, unknown>;
