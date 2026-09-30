import { ChangeEvent, useState } from "react";
import { evidenceLedger, ingestDataset, verifyEvidence } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import type { EvidenceVerification, LedgerEntry } from "../types";
import ErrorBoundary from "./ErrorBoundary";
import Skeleton from "./Skeleton";

type Result = { state: "checking" } | { state: "done"; v: EvidenceVerification } | { state: "error"; message: string };

function Badge({ entry, result }: { entry: LedgerEntry; result?: Result }) {
  if (result?.state === "checking") return <span className="tier-pill">checking…</span>;
  if (result?.state === "error") return <span className="tier-pill tier-medium" title={result.message}>could not verify</span>;
  if (result?.state === "done") {
    return result.v.intact
      ? <span className="wsrs-badge wsrs-low" title={result.v.delta_message}>✓ Verified</span>
      : <span className="wsrs-badge wsrs-high" title={result.v.delta_message}>✗ Tampered</span>;
  }
  // Not re-hashed yet: show what the ledger chain alone says.
  return entry.row_ok === false
    ? <span className="wsrs-badge wsrs-high" title="This ledger row no longer matches its hash chain">✗ Ledger altered</span>
    : <span className="tier-pill" title="Ledger row is consistent; press Verify to re-hash the stored file">Not yet verified</span>;
}

function Inner({ caseId, justification }: { caseId: string; justification?: string }) {
  const ledger = useAsync(() => evidenceLedger(caseId, justification));
  const [results, setResults] = useState<Record<string, Result>>({});
  const [uploadMsg, setUploadMsg] = useState("");

  async function verify(entry: LedgerEntry) {
    setResults((r) => ({ ...r, [entry.ledger_id]: { state: "checking" } }));
    try {
      const v = await verifyEvidence(entry.file_id, justification);
      setResults((r) => ({ ...r, [entry.ledger_id]: { state: "done", v } }));
    } catch (err) {
      setResults((r) => ({ ...r, [entry.ledger_id]: { state: "error", message: err instanceof Error ? err.message : "Verification failed" } }));
    }
  }

  async function verifyAll() {
    for (const e of ledger.data?.entries ?? []) await verify(e);
  }

  async function upload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadMsg(`Uploading ${file.name}…`);
    try {
      const res = await ingestDataset(file, caseId, justification);
      setUploadMsg(res.status === "duplicate" ? "That exact file was already uploaded." : `Recorded in the evidence ledger: ${file.name}`);
      ledger.reload();
    } catch (err) {
      setUploadMsg(err instanceof Error ? err.message : "Upload failed");
    }
    e.target.value = "";
  }

  if (ledger.loading && !ledger.data) return <section className="card"><Skeleton rows={4} /></section>;
  if (ledger.error) {
    return (
      <div className="panel-error" role="alert">
        <span>Couldn't load the evidence ledger: {ledger.error}</span>
        <button type="button" className="link-btn" onClick={ledger.reload}>Retry</button>
      </div>
    );
  }
  const data = ledger.data!;
  return (
    <section className="card">
      <div className="card-title">
        <h2 className="card-heading">Evidence chain of custody</h2>
        <span className={`wsrs-badge ${data.chain.valid ? "wsrs-low" : "wsrs-high"}`}>
          {data.chain.valid ? `Ledger chain intact (${data.chain.checked} entries)` : "Ledger chain BROKEN"}
        </span>
      </div>
      {!data.chain.valid && data.chain.first_break && (
        <p className="panel-error" role="alert">Tampering detected: {data.chain.first_break.reason} (entry {data.chain.first_break.ledger_id}).</p>
      )}
      <div className="page-actions" style={{ marginBottom: 12 }}>
        <label className="secondary btn-link" style={{ cursor: "pointer" }}>
          Attach evidence file
          <input type="file" onChange={upload} style={{ display: "none" }} />
        </label>
        <button type="button" className="secondary" onClick={() => void verifyAll()} disabled={!data.entries.length}>Verify all</button>
      </div>
      {uploadMsg && <p className="hint">{uploadMsg}</p>}
      <table className="audit-table">
        <thead><tr><th>File</th><th>SHA-256</th><th>Uploaded by</th><th>When</th><th>Status</th><th /></tr></thead>
        <tbody>
          {data.entries.map((e) => (
            <tr key={e.ledger_id}>
              <td title={e.file_id}>{e.file_name ?? e.file_id}</td>
              <td><code title={e.file_sha256}>{e.file_sha256.slice(0, 12)}…</code></td>
              <td>{e.uploaded_by}</td>
              <td>{new Date(e.uploaded_at).toLocaleString()}</td>
              <td><Badge entry={e} result={results[e.ledger_id]} /></td>
              <td><button type="button" className="link-btn" onClick={() => void verify(e)}>Verify</button></td>
            </tr>
          ))}
          {!data.entries.length && <tr><td colSpan={6} className="hint">No evidence files recorded for this case yet.</td></tr>}
        </tbody>
      </table>
      <p className="lead-note">Verify re-hashes the file currently in storage and compares it with the hash recorded at upload.</p>
    </section>
  );
}

export default function EvidenceTab(props: { caseId: string; justification?: string }) {
  return (
    <ErrorBoundary label="the evidence ledger">
      <Inner {...props} />
    </ErrorBoundary>
  );
}
