import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import ConfidencePill from "../components/ConfidencePill";
import { exportReport, getCase, listPatterns, logAudit } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { CaseDetail, PatternRecord } from "../types";

const ALL_SECTIONS = ["Entity List", "Notes", "Pattern Findings", "Source Citations"];

export default function ReportBuilder() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [caseRecord, setCaseRecord] = useState<CaseDetail | null>(null);
  const [patterns, setPatterns] = useState<PatternRecord[]>([]);
  const [sections, setSections] = useState<string[]>(ALL_SECTIONS);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [justification, setJustification] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    // A sensitive case is refused with 428 until a justification is supplied.
    // The workspace records one in sessionStorage when the analyst proceeds;
    // reuse it here so the report screen does not dead-end on "Loading".
    const stored = sessionStorage.getItem(`justification:${id}`) ?? undefined;
    getCase(id, stored)
      .then(setCaseRecord)
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Could not load case.";
        setLoadError(
          message.toLowerCase().includes("justif")
            ? "This case is access-restricted. Open it from the case workspace and state your justification first."
            : message
        );
      });
    listPatterns().then(setPatterns).catch((err: unknown) => {
      setLoadError(err instanceof Error ? err.message : "Could not load live patterns.");
    });
  }, [id]);

  function toggleSection(s: string) {
    setSections((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  }

  async function doExport() {
    if (!caseRecord || !user) return;
    if (caseRecord.is_sensitive && !justification.trim()) {
      setStatus("Justification is required to export a sensitive case.");
      return;
    }
    setBusy(true);
    try {
      const result = await exportReport(caseRecord, sections, justification || undefined);
      logAudit("export_report", caseRecord.title, user, justification || undefined);
      const url = URL.createObjectURL(result.blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${caseRecord.fir_number}-report.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      setStatus("Export logged to audit trail.");
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <main>
        <p className="error">{loadError}</p>
        <Link className="btn-link" to={`/cases/${id}`}>Open case workspace →</Link>
      </main>
    );
  }
  if (!caseRecord) return <main><p className="hint">Loading…</p></main>;

  return (
    <main>
      <PageHeader eyebrow={caseRecord.fir_number} title={`Report Builder — ${caseRecord.title}`}>
        Every AI-derived claim is confidence-tagged and cited. This is an investigative aid, not a final determination.
      </PageHeader>

      <div className="wizard-steps">
        {[1, 2, 3].map((n) => (
          <div key={n} className={`wizard-step ${step === n ? "wizard-step-active" : ""}`}>{n}. {n === 1 ? "Scope" : n === 2 ? "Sections" : "Preview & export"}</div>
        ))}
      </div>

      {step === 1 && (
        <section className="card">
          <p className="section-label">Scope</p>
          <p>Whole case: <strong>{caseRecord.title}</strong> ({caseRecord.entities.length} pinned entities, {caseRecord.notes.length} notes)</p>
          <p className="hint">Entity- and sub-network-scoped reports are on the roadmap; this build exports the full case.</p>
          <button onClick={() => setStep(2)}>Next: choose sections</button>
        </section>
      )}

      {step === 2 && (
        <section className="card">
          <p className="section-label">Sections to include</p>
          {ALL_SECTIONS.map((s) => (
            <label key={s} className="checkbox-row">
              <input type="checkbox" checked={sections.includes(s)} onChange={() => toggleSection(s)} />
              {s}
            </label>
          ))}
          <div className="page-actions" style={{ marginTop: 16 }}>
            <button className="secondary" onClick={() => setStep(1)}>Back</button>
            <button onClick={() => setStep(3)} disabled={!sections.length}>Next: preview</button>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="card">
          <p className="section-label">Preview</p>
          {sections.includes("Entity List") && (
            <div className="report-section">
              <h3>Entity list</h3>
              <ul>{caseRecord.entities.map((e) => <li key={e.entity_value}>{e.entity_value} <span className="source-chip">{e.entity_type}</span></li>)}</ul>
            </div>
          )}
          {sections.includes("Notes") && (
            <div className="report-section">
              <h3>Notes</h3>
              <ul>{caseRecord.notes.map((n) => <li key={n.note_id}>{n.content} — <em>{n.author}</em></li>)}</ul>
            </div>
          )}
          {sections.includes("Pattern Findings") && (
            <div className="report-section">
              <h3>Pattern findings</h3>
              <ul>{patterns.map((p) => <li key={p.pattern_id}>{p.description} <ConfidencePill confidence={p.confidence} /></li>)}</ul>
            </div>
          )}
          {sections.includes("Source Citations") && (
            <div className="report-section">
              <h3>Source citations</h3>
              <ul>{caseRecord.entities.map((e) => <li key={e.entity_value}><span className="source-chip">{caseRecord.fir_number}</span> {e.entity_value}</li>)}</ul>
            </div>
          )}

          {caseRecord.is_sensitive && (
            <label style={{ display: "block", marginTop: 18 }}>
              Export justification (required for sensitive case)
              <input value={justification} onChange={(e) => setJustification(e.target.value)} />
            </label>
          )}

          <div className="page-actions" style={{ marginTop: 16 }}>
            <button className="secondary" onClick={() => setStep(2)}>Back</button>
            <button onClick={doExport} disabled={busy}>{busy ? "Exporting…" : "Export report"}</button>
          </div>
          {status && <p className="hint">{status}</p>}
        </section>
      )}
    </main>
  );
}
