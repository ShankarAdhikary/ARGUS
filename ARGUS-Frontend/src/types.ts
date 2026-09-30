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

export interface RepeatVictimsResponse {
  repeat_victim_count: number;
  offense_categories: { category: string; victims: number }[];
  explanation: string;
  label: string;
}

export type WsrsTier = "HIGH" | "MEDIUM" | "LOW";

export interface WsrsFactor {
  score: number;
  label: string;
}

export interface WsrsBreakdown {
  total: number;
  tier: WsrsTier;
  factors: Record<"recency" | "repeat" | "escalation" | "network" | "geographic", WsrsFactor>;
  weights?: Record<string, number>;
  ws_fir_count: number;
  confidence: number;
  explanation: string;
  label: string;
}

export interface WsrsLeaderboardResponse {
  suspects: { suspect: string; score: number; tier: WsrsTier; wsrs: WsrsBreakdown }[];
  label: string;
}

export interface HotspotCollection {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    properties: { level: "high" | "medium" | "low"; cells: number };
    geometry: GeoJSON.MultiPolygon;
  }[];
  properties: { category: string | null; points: number; reason?: string; explanation?: string; confidence?: number; label: string };
}

export interface RiskForecastRow {
  jurisdiction: string;
  score: number;
  baseline_risk: number;
  projected_risk: number;
  shared_suspects: number;
  geographic_neighbour: boolean | null;
  explanation: string;
}

export interface RiskForecastResponse {
  jurisdiction: string;
  source_risk: number;
  forecast: RiskForecastRow[];
  method: string;
  confidence: number;
  label: string;
}

export interface LedgerEntry {
  ledger_id: string;
  file_id: string;
  file_name: string | null;
  file_sha256: string;
  uploaded_by: string;
  uploaded_at: string;
  size_bytes: number | null;
  row_hash: string;
  row_ok: boolean | null;
}

export interface LedgerResponse {
  entries: LedgerEntry[];
  chain: { valid: boolean; checked: number; first_break: { ledger_id: string; reason: string } | null };
}

export interface EvidenceVerification {
  intact: boolean;
  stored_hash: string;
  computed_hash: string | null;
  delta_message: string;
  chain_valid: boolean;
}

export interface VoiceEntity {
  type: string;
  value: string;
  confidence: number;
  evidence: string;
  canonical?: string | null;
}

export interface VoiceResult {
  transcript: string;
  language: string | null;
  entities: VoiceEntity[];
  suggested_fir_fields: {
    fields: { accused: string | null; mobile: string | null; location: string | null; station: string | null; date: string | null; sections: string[]; description: string };
    confidence: Record<string, number>;
    label: string;
  };
  message: string;
}
