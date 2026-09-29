import { FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { createCase, listCases } from "../lib/api";
import type { CaseSummary } from "../types";

export default function Cases() {
  const [cases, setCases]         = useState<CaseSummary[]>([]);
  const [loading, setLoading]     = useState(true);
  const [searchParams, setSearchParams] = useSearchParams();
  const [showForm, setShowForm]   = useState(searchParams.get("new") === "1");
  const [busy, setBusy]           = useState(false);
  const [error, setError]         = useState("");
  const [title, setTitle]         = useState("");
  const [firNumber, setFirNumber] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [isSensitive, setIsSensitive]   = useState(false);
  const [sensitivityReason, setSensitivityReason] = useState("");

  function refresh() {
    setLoading(true);
    listCases()
      .then(setCases)
      .catch(() => setError("Could not load cases."))
      .finally(() => setLoading(false));
  }

  useEffect(refresh, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      await createCase({ title, fir_number: firNumber, jurisdiction, is_sensitive: isSensitive, sensitivity_reason: isSensitive ? sensitivityReason : undefined });
      setTitle(""); setFirNumber(""); setJurisdiction(""); setIsSensitive(false); setSensitivityReason("");
      setShowForm(false);
      if (searchParams.has("new")) setSearchParams({}, { replace: true });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create case.");
    } finally {
      setBusy(false);
    }
  }

  const open   = cases.filter((c) => c.status === "open");
  const review = cases.filter((c) => c.status === "under_review");
  const closed = cases.filter((c) => c.status === "closed");

  return (
    <main>
      <PageHeader eyebrow="Investigation" title="Cases" actions={
        <button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "✕ Cancel" : "+ New case"}
        </button>
      }>
        Manage active investigations. Sensitive cases require justification to access and log every view to the audit trail.
      </PageHeader>

      {/* New case form */}
      {showForm && (
        <div className="card" style={{ marginBottom: 18 }}>
          <span className="section-label">Create new case</span>
          <form onSubmit={submit}>
            <div className="form-row">
              <label>
                Case title *
                <input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Trafficking Ring — Central District" />
              </label>
              <label>
                FIR number *
                <input required value={firNumber} onChange={(e) => setFirNumber(e.target.value)} placeholder="FIR-2026-XXX" />
              </label>
            </div>
            <label>
              Jurisdiction *
              <input required value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)} placeholder="e.g. Central District, Delhi" />
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={isSensitive} onChange={(e) => setIsSensitive(e.target.checked)} />
              Mark as sensitive (requires justification to access; every view is audited)
            </label>
            {isSensitive && (
              <label>
                Sensitivity reason
                <input value={sensitivityReason} onChange={(e) => setSensitivityReason(e.target.value)} placeholder="e.g. minor victim, witness protection" />
              </label>
            )}
            <div className="page-actions" style={{ marginTop: 4 }}>
              <button disabled={busy}>{busy ? "Creating…" : "Create case"}</button>
              <button type="button" className="secondary" onClick={() => setShowForm(false)}>Cancel</button>
            </div>
          </form>
          {error && <p className="error" style={{ marginTop: 10 }}>{error}</p>}
        </div>
      )}

      {error && !showForm && <p className="error" style={{ marginBottom: 16 }}>{error}</p>}
      {loading && <p className="hint" style={{ marginBottom: 16 }}>Loading cases…</p>}

      {[
        { label: "Open", items: open,   status: "open" },
        { label: "Under Review", items: review, status: "under_review" },
        { label: "Closed", items: closed, status: "closed" },
      ].filter((g) => g.items.length > 0).map((group) => (
        <div key={group.status} style={{ marginBottom: 22 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span className={`status-pill status-${group.status}`}>{group.label}</span>
            <span className="hint">{group.items.length} case{group.items.length === 1 ? "" : "s"}</span>
          </div>
          <div className="case-list">
            {group.items.map((c) => (
              <Link key={c.case_id} to={`/cases/${c.case_id}`} className="case-row">
                <div className="case-row-info">
                  <span className="case-row-title">{c.title}</span>
                  <span className="case-row-meta">{c.fir_number} · {c.jurisdiction} · {new Date(c.opened_at).toLocaleDateString("en-IN")}</span>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
                  {c.is_sensitive && <span className="sensitive-tag">sensitive</span>}
                  {c.category && <span className="source-chip">{c.category}</span>}
                </div>
              </Link>
            ))}
          </div>
        </div>
      ))}

      {!loading && !cases.length && (
        <div className="card" style={{ textAlign: "center", padding: "48px 20px" }}>
          <p style={{ fontSize: "1.05rem", fontWeight: 700, marginBottom: 8 }}>No cases yet</p>
          <p className="hint" style={{ marginBottom: 16 }}>Create your first investigation case to start pinning entities and building evidence.</p>
          <button onClick={() => setShowForm(true)}>+ Create first case</button>
        </div>
      )}
    </main>
  );
}
