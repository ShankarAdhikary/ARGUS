import { ChangeEvent, FormEvent, useCallback, useEffect, useState } from "react";
import PageHeader from "../components/PageHeader";
import VoiceFir from "../components/VoiceFir";
import { getJob, health, ingestDataset, ingestText, resolveCheck, resolveDecision } from "../lib/api";
import type { ResolveCandidate } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { ApiRecord } from "../types";

export default function Ingestion() {
  const { user } = useAuth();
  // The server only lets these roles load data; analysts can still check aliases and job status.
  const canIngest = user?.role !== "analyst";
  const [status, setStatus] = useState<{ status: string; dependencies: Record<string, boolean> } | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sourceId, setSourceId] = useState("FIR-2026-001");
  const [text, setText] = useState("FIR-2026-001 states that Suspect Vikram Singh used mobile 9999988888 near Central Bus Terminal.");
  const [jobId, setJobId] = useState("");
  const [resolveName, setResolveName] = useState("Vikram Sinh");
  const [candidates, setCandidates] = useState<ResolveCandidate[] | null>(null);
  const [decided, setDecided] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ApiRecord | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const checkHealth = useCallback(async () => {
    try {
      setStatus(await health());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => { void checkHealth(); }, [checkHealth]);

  async function run(action: () => Promise<ApiRecord>) {
    setBusy(true);
    setError("");
    try {
      const data = await action();
      setResult(data);
      const returnedJob = data.job_id;
      if (typeof returnedJob === "string") setJobId(returnedJob);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  function uploadDataset(event: FormEvent) {
    event.preventDefault();
    if (!file) { setError("Choose a FIR or CDR JSON file first."); return; }
    void run(() => ingestDataset(file));
  }

  function extractText(event: FormEvent) {
    event.preventDefault();
    void run(() => ingestText(sourceId, text));
  }

  async function checkResolve(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      setCandidates(await resolveCheck(resolveName));
      setDecided({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    }
  }

  async function decide(candidate: ResolveCandidate, decision: "confirm_merge" | "reject") {
    setError("");
    try {
      const res = await resolveDecision(resolveName, candidate.candidate, decision, candidate.similarity);
      setDecided((prev) => ({
        ...prev,
        [candidate.candidate]: decision === "reject" ? "Rejected" : res.graph_linked ? "Confirmed — alias linked in graph" : "Confirmed — recorded (no matching graph node)",
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    }
  }

  return (
    <main>
      <PageHeader eyebrow="Data ingestion" title="Ingestion Console">
        Upload structured FIR/CDR batches or extract entities from unstructured narrative. Every candidate is confidence-scored before it reaches the graph.
      </PageHeader>

      {!canIngest && (
        <p className="login-notice warn" role="status">
          Your role (analyst) can't upload or extract data — ask an investigator or supervisor to ingest it. You can still check aliases and job status below.
        </p>
      )}

      {/* Pipeline status bar */}
      <div className="status-card">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className={`dot ${status?.status === "healthy" ? "ok" : "warn"}`} />
          <span style={{ fontWeight: 700, fontSize: "0.875rem" }}>
            Pipeline {status?.status === "healthy" ? "healthy" : "degraded or unreachable"}
          </span>
        </div>
        <div className="dependencies">
          {status
            ? Object.entries(status.dependencies).map(([name, ready]) => (
                <span key={name} className={ready ? "ready" : "down"}>{name}: {ready ? "ready" : "down"}</span>
              ))
            : <span className="down">Start the API with Docker Compose.</span>}
        </div>
      </div>

      <div className="grid">
        {/* Structured data */}
        <section className="card">
          <span className="section-label">01 · Structured data</span>
          <h2>Queue FIR or CDR JSON</h2>
          <form onSubmit={uploadDataset}><fieldset disabled={!canIngest || busy} className="bare-fieldset">
            <input
              aria-label="FIR or CDR JSON file"
              type="file"
              accept="application/json,.json"
              onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)}
            />
            <button disabled={busy || !file}>Queue dataset</button>
          </fieldset></form>
          <p className="hint" style={{ marginTop: 8 }}>Use <code>demo_firs.json</code> / <code>demo_cdrs.json</code> from ARGUS-Pipeline.</p>
        </section>

        {/* Zero-shot extraction */}
        <section className="card">
          <span className="section-label">02 · Zero-shot extraction</span>
          <h2>Extract messy narrative</h2>
          {canIngest && <VoiceFir onUseTranscript={(t) => setText(t)} />}
          <form onSubmit={extractText}><fieldset disabled={!canIngest || busy} className="bare-fieldset">
            <label>Source ID<input value={sourceId} onChange={(event) => setSourceId(event.target.value)} /></label>
            <label>Investigative text<textarea rows={4} value={text} onChange={(event) => setText(event.target.value)} /></label>
            <button disabled={busy}>Extract candidates</button>
          </fieldset></form>
          <p className="hint" style={{ marginTop: 8 }}>Uses Groq when configured; otherwise deterministic regex fallback.</p>
        </section>

        {/* Entity resolution */}
        <section className="card">
          <span className="section-label">03 · Entity resolution check</span>
          <h2>Fuzzy alias match</h2>
          <form onSubmit={checkResolve}>
            <label>Candidate name<input value={resolveName} onChange={(event) => setResolveName(event.target.value)} /></label>
            <button disabled={busy}>Check for aliases</button>
          </form>
          {candidates && (
            <div style={{ marginTop: 12 }}>
              {candidates.length === 0 && <p className="hint">No similar identities found.</p>}
              {candidates.map((c) => (
                <div className="candidate-row" key={c.candidate}>
                  <span className="candidate-name">{c.candidate}</span>
                  <span className="hint">{Math.round(c.similarity * 100)}%{c.suggested === "merge" ? " · likely same person" : ""}</span>
                  {decided[c.candidate]
                    ? <span className="hint">{decided[c.candidate]}</span>
                    : <>
                        <button type="button" onClick={() => void decide(c, "confirm_merge")}>Confirm same person</button>
                        <button type="button" className="secondary" onClick={() => void decide(c, "reject")}>Not a match</button>
                      </>}
                </div>
              ))}
            </div>
          )}
          <p className="hint" style={{ marginTop: 8 }}>Identities are never merged automatically — an analyst must confirm each link. Confirmed links are reversible aliases.</p>
        </section>

        {/* Job status */}
        <section className="card">
          <span className="section-label">04 · Pipeline status</span>
          <h2>Check a queued job</h2>
          <form onSubmit={(event) => { event.preventDefault(); if (jobId) void run(() => getJob(jobId)); }}>
            <label>Job ID<input placeholder="Created after queueing" value={jobId} onChange={(event) => setJobId(event.target.value)} /></label>
            <button disabled={busy || !jobId}>Check status</button>
          </form>
        </section>
      </div>

      {error && <p className="error" role="alert" style={{ marginTop: 14 }}>{error}</p>}
      {result && (
        <section className="result" style={{ marginTop: 16 }}>
          <h3>Pipeline response</h3>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </section>
      )}
    </main>
  );
}
