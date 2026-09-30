import { useEffect, useState } from "react";
import PageHeader from "../components/PageHeader";
import { adminMfaReset, auditLog, health, ingestionHealth } from "../lib/api";
import type { AuditEntry, IngestionHealth } from "../types";
import { useT } from "../i18n";

export default function Admin() {
  const t = useT();
  const [ingestion, setIngestion] = useState<IngestionHealth | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [deps, setDeps] = useState<Record<string, boolean> | null>(null);
  const [auditFilter, setAuditFilter] = useState("");
  const [resetId, setResetId] = useState("");
  const [resetMsg, setResetMsg] = useState("");

  useEffect(() => {
    ingestionHealth().then(setIngestion).catch(() => {});
    auditLog().then(setAudit).catch(() => {});
    health().then((h) => setDeps(h.dependencies)).catch(() => setDeps(null));
  }, []);

  const filteredAudit = audit.filter((a) =>
    `${a.user} ${a.action} ${a.resource}`.toLowerCase().includes(auditFilter.toLowerCase())
  );

  return (
    <main>
      <PageHeader eyebrow={t("admin.eyebrow")} title={t("admin.title")}>
        Ingestion health, infrastructure status, and tamper-evident audit log. Role: admin only.
      </PageHeader>

      {/* Infrastructure status */}
      <span className="section-label">Infrastructure</span>
      <div className="infra-grid" style={{ marginBottom: 24 }}>
        {deps
          ? Object.entries(deps).map(([name, ready]) => (
              <div key={name} className={`infra-tile ${ready ? "ok" : "down"}`}>
                <span className={`dot ${ready ? "ok" : "alert"}`} />
                <span className={`infra-status ${ready ? "" : ""}`} style={{ color: ready ? "var(--green)" : "var(--red)" }}>
                  {ready ? "Online" : "Down"}
                </span>
                <span className="infra-name">{name}</span>
              </div>
            ))
          : <p className="hint">Start the API with Docker Compose to see infrastructure health.</p>}
      </div>

      {/* Ingestion health */}
      <span className="section-label">Ingestion pipeline</span>
      {ingestion && (
        <div className="status-card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className={`dot ${ingestion.status === "healthy" ? "ok" : "warn"}`} />
            <span style={{ fontWeight: 700, fontSize: "0.875rem", color: ingestion.status === "healthy" ? "var(--green)" : "var(--amber)" }}>
              {ingestion.status === "healthy" ? "Healthy" : "Needs attention"}
            </span>
            {ingestion.lastRun && (
              <span className="hint">Last run: {new Date(ingestion.lastRun).toLocaleString("en-IN")}</span>
            )}
          </div>
        </div>
      )}
      <div className="stat-row" style={{ marginBottom: 24 }}>
        <div className="stat-tile"><span className="stat-value">{ingestion?.queued ?? "—"}</span><span className="stat-label">Queued</span></div>
        <div className="stat-tile"><span className="stat-value">{ingestion?.processing ?? "—"}</span><span className="stat-label">Processing</span></div>
        <div className="stat-tile stat-success"><span className="stat-value">{ingestion?.processed ?? "—"}</span><span className="stat-label">Processed</span></div>
        <div className="stat-tile stat-warn"><span className="stat-value">{ingestion?.quarantined ?? "—"}</span><span className="stat-label">Quarantined</span></div>
      </div>

      {/* Account recovery */}
      <span className="section-label">Account recovery</span>
      <form
        className="status-card"
        style={{ marginBottom: 24, gap: 10, flexWrap: "wrap" }}
        onSubmit={(e) => {
          e.preventDefault();
          if (!resetId.trim() || !window.confirm(`Reset two-step verification for ${resetId.trim()}? They will have to enrol again.`)) return;
          adminMfaReset(resetId.trim())
            .then(() => setResetMsg(`Two-step verification reset for ${resetId.trim()}.`))
            .catch((err: unknown) => setResetMsg(err instanceof Error ? err.message : "Reset failed."));
        }}
      >
        <input value={resetId} onChange={(e) => setResetId(e.target.value)} placeholder="Employee ID who lost their authenticator" style={{ flex: 1, minWidth: 240 }} />
        <button className="secondary" disabled={!resetId.trim()}>Reset MFA</button>
        {resetMsg && <span className="hint" role="status">{resetMsg}</span>}
      </form>

      {/* Audit log */}
      <div className="card">
        <div className="card-title">
          <span>Audit log</span>
          <span className="hint">{audit.length} entries</span>
        </div>
        <input
          placeholder="Filter by user, action, or resource…"
          value={auditFilter}
          onChange={(e) => setAuditFilter(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        <div className="audit-scroll">
          <table className="audit-table">
            <thead>
              <tr>
                <th>User</th><th>Role</th><th>Action</th><th>Resource</th><th>Justification</th><th>When</th>
              </tr>
            </thead>
            <tbody>
              {filteredAudit.map((a) => (
                <tr key={a.audit_id}>
                  <td style={{ fontWeight: 600, color: "var(--text)" }}>{a.user}</td>
                  <td><span className={`role-badge role-${a.role}`} style={{ fontSize: "0.75rem", padding: "2px 6px", borderRadius: "100px" }}>{a.role}</span></td>
                  <td style={{ fontFamily: "var(--mono, monospace)", fontSize: "0.75rem" }}>{a.action}</td>
                  <td style={{ color: "var(--text-3)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.resource}</td>
                  <td style={{ color: "var(--text-3)", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }}>{a.justification ?? "—"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{new Date(a.occurred_at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}</td>
                </tr>
              ))}
              {!filteredAudit.length && (
                <tr><td colSpan={6} className="hint" style={{ textAlign: "center", padding: 20 }}>No matching entries.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </main>
  );
}
