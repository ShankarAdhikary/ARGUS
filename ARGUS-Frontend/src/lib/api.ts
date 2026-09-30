import type {
  AlertRecord,
  AlertRule,
  AuditEntry,
  AuthUser,
  CaseDetail,
  CaseEntityLink,
  CaseSummary,
  CentralityRow,
  GraphElements,
  EntityType,
  IngestionHealth,
  PatternRecord,
  RepeatVictimsResponse,
  WsrsBreakdown,
  HotspotCollection,
  LedgerResponse,
  EvidenceVerification,
  VoiceResult,
  FingerprintMatchResult,
  FingerprintEnrollResult,
  FingerprintStatus,
  RiskForecastResponse,
  WsrsLeaderboardResponse,
  VoiceprintStatus,
  VoiceprintMatchResult,
  VoiceprintEnrollResult,
  ResolutionDecision,
  Role,
} from "../types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const TOKEN_KEY = "argus_token";
const USER_KEY = "argus_user";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): AuthUser | null {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? (JSON.parse(raw) as AuthUser) : null;
}

export function storeSession(token: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

const REQUEST_TIMEOUT_MS = 30_000;
// Face-model inference on CPU can take a while, especially on the first call after a restart.
const BIOMETRIC_TIMEOUT_MS = 120_000;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), path.startsWith("/api/v1/biometric") || path.startsWith("/api/v1/ingest/voice") ? BIOMETRIC_TIMEOUT_MS : REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller.signal,
    });
  } catch (err) {
    clearTimeout(timeoutId);
    if (err instanceof Error && err.name === "AbortError") {
      throw new ApiError("Request timed out. The server may be slow — please try again.", 408);
    }
    throw new ApiError("Network error — is the server running?", 0);
  }
  clearTimeout(timeoutId);

  // Session expired — redirect to login without throwing a cryptic error
  // Wrong passwords / wrong MFA codes are 401s too, but they are form errors, not an expired session.
  if (response.status === 401 && !path.startsWith("/api/v1/auth/login") && !path.startsWith("/api/v1/auth/mfa/")) {
    clearSession();
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.replace(`/login?expired=1&next=${next}`);
    throw new ApiError("Session expired. Redirecting to sign-in…", 401);
  }

  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json")
    ? await response.json().catch(() => ({}))
    : await response.text();

  if (!response.ok) {
    const detail = typeof body === "object" && body && "detail" in body ? (body as Record<string, unknown>).detail : undefined;
    const message =
      detail !== undefined
        // Structured errors (e.g. a fingerprint that is too poor to enrol) carry their text in detail.message.
        ? typeof detail === "object" && detail && "message" in detail ? String((detail as Record<string, unknown>).message) : String(detail)
        : typeof body === "object" && body && "message" in body
        ? String((body as Record<string, unknown>).message)
        : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail && typeof detail === "object" ? (detail as Record<string, unknown>) : undefined);
  }
  return body as T;
}

async function requestBlob(path: string, options: RequestInit = {}): Promise<Blob> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(body || `Request failed (${response.status})`, response.status);
  }
  return response.blob();
}

export class ApiError extends Error {
  status: number;
  /** The structured `detail` object when the API sent one (e.g. quality_check for a fingerprint that was refused). */
  detail?: Record<string, unknown>;
  constructor(message: string, status: number, detail?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

// ---------------------------------------------------------------------------
// Live endpoints (already implemented on the backend)
// ---------------------------------------------------------------------------

export function health() {
  return request<{ status: string; dependencies: Record<string, boolean> }>("/health");
}

export function ingestDataset(file: File, caseId?: string, justification?: string) {
  const form = new FormData();
  form.append("file", file);
  if (caseId) form.append("case_id", caseId);
  if (justification) form.append("justification", justification);
  return request<Record<string, unknown>>("/api/v1/ingest", { method: "POST", body: form });
}

export function ingestVoice(audio: Blob, filename: string, language?: string) {
  const form = new FormData();
  form.append("file", audio, filename);
  if (language) form.append("language", language);
  return request<VoiceResult>("/api/v1/ingest/voice", { method: "POST", body: form });
}

export function ingestText(sourceId: string, text: string, sourceType = "fir") {
  return request<Record<string, unknown>>("/api/v1/ingest/text", {
    method: "POST",
    body: JSON.stringify({ source_id: sourceId, text, source_type: sourceType }),
  });
}

export function getJob(jobId: string) {
  return request<Record<string, unknown>>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
}

export function searchFirs(q: string) {
  return request<{ status: string; count: number; results: ApiRecordList }>(`/api/v1/search/firs?q=${encodeURIComponent(q)}`);
}

export function searchMasterDossier(query: string) {
  return request<{ status: string; total_matches: number; dossier_records: ApiRecordList }>(
    `/api/v1/search/master-dossier?query=${encodeURIComponent(query)}`
  );
}

export function searchJudgesView(prison = "All") {
  return request<{ status: string; count: number; results: ApiRecordList }>(
    `/api/v1/search/judges-view?prison=${encodeURIComponent(prison)}`
  );
}

export function networkAccused(accusedName: string, caseId?: string, justification?: string, before?: string) {
  const params = new URLSearchParams({ accused_name: accusedName });
  if (caseId) params.set("case_id", caseId);
  if (justification) params.set("justification", justification);
  if (before) params.set("before", before);
  return request<Record<string, unknown>>(`/api/v1/network/accused?${params}`);
}

export function networkPath(source: string, target: string, caseId?: string, justification?: string) {
  const params = new URLSearchParams({ source, target });
  if (caseId) params.set("case_id", caseId);
  if (justification) params.set("justification", justification);
  return request<{ status: string; nodes: GraphElements["nodes"]; edges: GraphElements["edges"] }>(
    `/api/v1/network/path?${params}`,
  );
}

export function networkPhone(phoneNumber: string, caseId?: string, justification?: string) {
  const params = new URLSearchParams({ phone_number: phoneNumber });
  if (caseId) params.set("case_id", caseId);
  if (justification) params.set("justification", justification);
  return request<Record<string, unknown>>(`/api/v1/network/phone?${params}`);
}

export function networkFinancial(accountId: string, caseId?: string, justification?: string) {
  const params = new URLSearchParams({ account_id: accountId });
  if (caseId) params.set("case_id", caseId);
  if (justification) params.set("justification", justification);
  return request<Record<string, unknown>>(`/api/v1/network/financial?${params}`);
}

export function burners(threshold = 2) {
  return request<{ status: string; total_flagged: number; burners: Array<{ phone: string; calls: number }> }>(
    `/api/v1/analytics/burners?threshold=${threshold}`
  );
}

export function biometricEnroll(name: string, file: File) {
  const form = new FormData();
  form.append("name", name);
  form.append("file", file);
  return request<Record<string, unknown>>("/api/v1/biometric/enroll", { method: "POST", body: form });
}

export function biometricHunt(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<{ status: string; match_found: boolean; confidence_score?: number; suspect_data?: Record<string, unknown> }>(
    "/api/v1/biometric/hunt",
    { method: "POST", body: form }
  );
}

export function biometricUnifiedEnroll(fields: Record<string, string>, file: File) {
  const form = new FormData();
  Object.entries(fields).forEach(([key, value]) => form.append(key, value));
  form.append("file", file);
  return request<Record<string, unknown>>("/api/v1/biometric/unified-enroll", { method: "POST", body: form });
}

export function fingerprintStatus() {
  return request<FingerprintStatus>("/api/v1/biometric/fingerprint/status");
}

export function fingerprintMatch(file: File, printType: "rolled" | "latent" = "rolled", caseId?: string, justification?: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("print_type", printType);
  if (caseId) form.append("case_id", caseId);
  if (justification) form.append("justification", justification);
  return request<FingerprintMatchResult>("/api/v1/biometric/fingerprint/match", { method: "POST", body: form });
}

export function fingerprintEnroll(fields: { name: string; fir_id: string }, file: File) {
  const form = new FormData();
  form.append("name", fields.name);
  form.append("fir_id", fields.fir_id);
  form.append("file", file);
  return request<FingerprintEnrollResult>("/api/v1/biometric/fingerprint/enroll", { method: "POST", body: form });
}

/**
 * Voice biometrics. The routes only exist when the deployment has switched the feature on (VOICEPRINT_ENABLED); until the
 * evaluation has been run they answer 404. That is an expected state, not an error, so it maps to `available: false`.
 */
export async function voiceprintStatus(): Promise<VoiceprintStatus> {
  try {
    const s = await request<{ available: boolean; calibrated: boolean; enrolled: number; message: string | null; label: string }>("/api/v1/biometric/voice/status");
    return { available: s.available, enabled: true, calibrated: s.calibrated, enrolled: s.enrolled, reason: s.message ?? undefined, label: s.label };
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return { available: false, enabled: false, calibrated: false, enrolled: 0,
               reason: "Speaker identification is switched off on this deployment until it has been evaluated on real recordings." };
    }
    throw err;
  }
}

export function voiceprintMatch(file: File, lawfulInterceptionRef: string, caseId?: string, justification?: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("lawful_interception_ref", lawfulInterceptionRef);
  if (caseId) form.append("case_id", caseId);
  if (justification) form.append("justification", justification);
  return request<VoiceprintMatchResult>("/api/v1/biometric/voice/match", { method: "POST", body: form });
}

export function voiceprintEnroll(fields: { name: string; fir_id: string; lawful_interception_ref: string }, file: File) {
  const form = new FormData();
  form.append("name", fields.name);
  form.append("fir_id", fields.fir_id);
  form.append("lawful_interception_ref", fields.lawful_interception_ref);
  form.append("file", file);
  return request<VoiceprintEnrollResult>("/api/v1/biometric/voice/enroll", { method: "POST", body: form });
}

export function biometricBulkZip(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<Record<string, unknown>>("/api/v1/biometric/bulk-upload-zip", { method: "POST", body: form });
}

export function deleteByFir(firId: string) {
  return request<Record<string, unknown>>(`/api/v1/target/delete-by-fir?fir_id=${encodeURIComponent(firId)}`, {
    method: "DELETE",
  });
}

type ApiRecordList = Array<Record<string, unknown>>;

// ---------------------------------------------------------------------------
// Platform endpoints. Errors are intentionally propagated to the UI.
// ---------------------------------------------------------------------------

interface RawSession {
  access_token: string; role: Role; full_name: string; user_id: string; employee_id: string; jurisdiction: string;
  recovery_codes?: string[]; recovery_codes_left?: number;
}

export type LoginResult =
  | { kind: "session"; token: string; user: AuthUser }
  | { kind: "mfa"; mfaToken: string; enrollment: boolean };

export interface MfaSession { token: string; user: AuthUser; recoveryCodes?: string[]; recoveryCodesLeft?: number }

function toSession(r: RawSession): MfaSession {
  return {
    token: r.access_token,
    user: { user_id: r.user_id, employee_id: r.employee_id, full_name: r.full_name, role: r.role, jurisdiction: r.jurisdiction },
    recoveryCodes: r.recovery_codes,
    recoveryCodesLeft: r.recovery_codes_left,
  };
}

export function login(employeeId: string, password: string): Promise<LoginResult> {
  return request<RawSession | { mfa_required: true; enrollment_required: boolean; mfa_token: string }>(
    "/api/v1/auth/login",
    { method: "POST", body: JSON.stringify({ employee_id: employeeId, password }) }
  ).then((r) =>
    "mfa_required" in r
      ? { kind: "mfa" as const, mfaToken: r.mfa_token, enrollment: r.enrollment_required }
      : { kind: "session" as const, ...toSession(r) }
  );
}

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const mfaVerify = (mfaToken: string, code: string) =>
  post<RawSession>("/api/v1/auth/mfa/verify", { mfa_token: mfaToken, code }).then(toSession);
export const mfaEnrollBegin = (mfaToken: string) =>
  post<{ secret: string; otpauth_uri: string }>("/api/v1/auth/mfa/enroll/begin", { mfa_token: mfaToken });
export const mfaEnrollComplete = (mfaToken: string, code: string) =>
  post<RawSession>("/api/v1/auth/mfa/enroll/complete", { mfa_token: mfaToken, code }).then(toSession);
export const mfaStatus = () => request<{ enabled: boolean; required: boolean; recovery_codes_left: number }>("/api/v1/auth/mfa/status");
export const mfaSetup = () => post<{ secret: string; otpauth_uri: string }>("/api/v1/auth/mfa/setup", {});
export const mfaEnable = (code: string) => post<{ enabled: boolean; recovery_codes: string[] }>("/api/v1/auth/mfa/enable", { code });
export const mfaDisable = (code: string) => post<{ enabled: boolean }>("/api/v1/auth/mfa/disable", { code });
export const adminMfaReset = (employeeId: string) =>
  post<{ employee_id: string; mfa_enabled: boolean }>(`/api/v1/admin/users/${encodeURIComponent(employeeId)}/mfa-reset`, {});

export function me() {
  return request<AuthUser>("/api/v1/auth/me");
}

export function listCases() {
  return request<CaseSummary[]>("/api/v1/cases");
}

export function createCase(payload: Omit<CaseSummary, "case_id" | "opened_at" | "status">) {
  return request<CaseSummary>("/api/v1/cases", { method: "POST", body: JSON.stringify(payload) });
}

export function getCase(caseId: string, justification?: string) {
  const qs = justification ? `?justification=${encodeURIComponent(justification)}` : "";
  return request<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}${qs}`);
}

export function addCaseEntity(caseId: string, link: Omit<CaseEntityLink, "linked_at" | "linked_by">, actor: string) {
  void actor;
  return request<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}/entities`, {
    method: "POST",
    body: JSON.stringify(link),
  });
}

export function addCaseNote(caseId: string, content: string, author: string) {
  void author;
  return request<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}/notes`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function listPatterns() {
  return request<PatternRecord[]>("/api/v1/patterns");
}

export function patternFeedback(patternId: string, verdict: "useful" | "false_positive" | "escalated") {
  return request<PatternRecord>(`/api/v1/patterns/${encodeURIComponent(patternId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ verdict }),
  });
}

export function centrality() {
  return request<CentralityRow[]>("/api/v1/analytics/centrality");
}

export function repeatVictims() {
  return request<RepeatVictimsResponse>("/api/v1/analytics/repeat-victims");
}

export function personWsrs(person: string) {
  return request<{ person: string; wsrs: WsrsBreakdown | null }>(`/api/v1/analytics/wsrs?person_name=${encodeURIComponent(person)}`);
}

export function wsrsLeaderboard(options: number | { limit?: number; tier?: string; jurisdiction?: string } = 5) {
  const o = typeof options === "number" ? { limit: options } : options;
  const q = new URLSearchParams({ limit: String(o.limit ?? 50) });
  if (o.tier) q.set("tier", o.tier);
  if (o.jurisdiction) q.set("jurisdiction", o.jurisdiction);
  return request<WsrsLeaderboardResponse>(`/api/v1/analytics/wsrs-leaderboard?${q}`);
}

export function hotspots(category = "WOMEN_SAFETY") {
  return request<HotspotCollection>(`/api/v1/analytics/hotspots?category=${encodeURIComponent(category)}`);
}

export function riskForecast(jurisdiction: string) {
  return request<RiskForecastResponse>(`/api/v1/analytics/risk-forecast?jurisdiction=${encodeURIComponent(jurisdiction)}`);
}

export function evidenceLedger(caseId: string, justification?: string) {
  const q = new URLSearchParams({ case_id: caseId });
  if (justification) q.set("justification", justification);
  return request<LedgerResponse>(`/api/v1/evidence/ledger?${q}`);
}

export function verifyEvidence(fileId: string, justification?: string) {
  const q = justification ? `?justification=${encodeURIComponent(justification)}` : "";
  return request<EvidenceVerification>(`/api/v1/evidence/verify/${fileId.split("/").map(encodeURIComponent).join("/")}${q}`);
}

export interface ResolveCandidate {
  candidate: string;
  similarity: number;
  match_type?: string;
  /** Script-independent form of the candidate ("Ramesh", "रमेश" and "RAMESH" all give "ramesh"). */
  canonical?: string;
  resolution?: string;
  suggested?: "merge" | "possible_match";
  label?: string;
}

export function resolutionDecisions(limit = 10) {
  return request<ResolutionDecision[]>(`/api/v1/resolve/decisions?limit=${limit}`);
}

export function resolveCheck(name: string) {
  return request<ResolveCandidate[]>(`/api/v1/resolve/check?name=${encodeURIComponent(name)}`);
}

export function resolveDecision(name: string, candidate: string, decision: "confirm_merge" | "reject", similarity?: number) {
  return request<{ decision_id: string; decision: string; graph_linked: boolean }>("/api/v1/resolve/decision", {
    method: "POST",
    body: JSON.stringify({ name, candidate, decision, similarity }),
  });
}

export function listAlerts() {
  return request<AlertRecord[]>("/api/v1/alerts");
}

export const markAlertRead = (alertId: string) =>
  request<{ alert_id: string; read_status: boolean }>(`/api/v1/alerts/${encodeURIComponent(alertId)}/read`, { method: "POST" });
export const markAllAlertsRead = () => request<{ updated: number }>("/api/v1/alerts/read-all", { method: "POST" });

export interface DashboardSummary {
  firs: number; suspects: number; phones: number; accounts: number; calls: number; transactions: number; sightings: number;
}
export const dashboardSummary = () => request<DashboardSummary>("/api/v1/dashboard/summary");

export function listAlertRules() {
  return request<AlertRule[]>("/api/v1/alerts/rules");
}

export function createAlertRule(entityValue: string) {
  return request<AlertRule>("/api/v1/alerts/rules", { method: "POST", body: JSON.stringify({ entity_value: entityValue }) });
}

export function auditLog() {
  return request<AuditEntry[]>("/api/v1/audit");
}

/**
 * Audit records are written by the backend on every protected operation.
 * Kept as a compatibility shim for pages that previously emitted client-only
 * audit entries; it never creates or stores local data.
 */
export function logAudit(_action: string, _resource: string, _user: AuthUser, _justification?: string): void {
  return;
}

export function ingestionHealth() {
  return request<IngestionHealth>("/api/v1/admin/ingestion-health");
}

export type ReportFormat = "pdf" | "docx";

export interface ReportExportResult {
  mode: ReportFormat;
  blob: Blob;
}

export async function exportReport(caseRecord: CaseDetail, sections: string[], justification?: string, format: ReportFormat = "pdf"): Promise<ReportExportResult> {
  const blob = await requestBlob(`/api/v1/reports/export?format=${format}`, {
    method: "POST",
    body: JSON.stringify({ case_id: caseRecord.case_id, sections, justification }),
  });
  return { mode: format, blob };
}

export function entityTypeGuess(value: string): EntityType {
  const trimmed = value.trim();
  if (/^[+]?\d[\d\s-]{6,}$/.test(trimmed)) return "phone";
  if (/^[A-Z]{2}[- ]?\d{1,2}[- ]?[A-Z]{0,2}[- ]?\d{3,4}$/i.test(trimmed)) return "vehicle";
  return "person";
}
