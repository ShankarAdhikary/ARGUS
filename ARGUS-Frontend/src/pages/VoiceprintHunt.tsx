import { ChangeEvent, FormEvent, ReactNode, useEffect, useState } from "react";
import ErrorBoundary from "../components/ErrorBoundary";
import LeadNotice from "../components/LeadNotice";
import PageHeader from "../components/PageHeader";
import Skeleton from "../components/Skeleton";
import { ApiError, voiceprintEnroll, voiceprintMatch, voiceprintStatus } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAsync } from "../lib/useAsync";
import { usePageTitle } from "../lib/usePageTitle";
import type { VoiceprintEnrollResult, VoiceprintMatchResult, VoiceprintQuality, VoiceprintStatus } from "../types";

const ACCEPT = ".wav,.mp3,.m4a,.ogg";
const FALLBACK_LABEL = "Investigative lead — requires forensic voice expert confirmation before use in proceedings.";

/** Length of the chosen recording, read from the browser (null while loading or if it cannot be decoded). */
function useAudioDuration(file: File | null): number | null {
  const [seconds, setSeconds] = useState<number | null>(null);
  useEffect(() => {
    setSeconds(null);
    if (!file) return;
    const url = URL.createObjectURL(file);
    const audio = new Audio();
    audio.preload = "metadata";
    audio.onloadedmetadata = () => setSeconds(Number.isFinite(audio.duration) ? audio.duration : null);
    audio.src = url;
    return () => {
      audio.onloadedmetadata = null;
      URL.revokeObjectURL(url);
    };
  }, [file]);
  return seconds;
}

function AuthorizationField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label>
      Authorization reference <span style={{ color: "var(--red)" }} aria-hidden="true">*</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} required minLength={3} maxLength={100} placeholder="e.g. LI/MHA/2026/00417" aria-describedby="lir-help" />
      <span id="lir-help" className="hint">Enter the lawful interception order number. Required for audit: it is recorded with every enrolment and search.</span>
    </label>
  );
}

function AudioField({ file, onFile, disabled }: { file: File | null; onFile: (f: File | null) => void; disabled?: boolean }) {
  const seconds = useAudioDuration(file);
  return (
    <label>
      Audio recording <span style={{ color: "var(--red)" }} aria-hidden="true">*</span>
      <input type="file" accept={ACCEPT} required disabled={disabled} onChange={(e: ChangeEvent<HTMLInputElement>) => onFile(e.target.files?.[0] ?? null)} />
      <span className="hint">
        WAV, MP3, M4A or OGG, one speaker per recording, at least 3 seconds of speech.
        {file && <> Selected: {file.name} · {seconds === null ? "reading length…" : `${seconds.toFixed(1)} s`}</>}
      </span>
    </label>
  );
}

function Quality({ q }: { q: VoiceprintQuality }) {
  return (
    <p className="hint" style={{ margin: "8px 0" }}>
      Audio: {q.duration_seconds !== undefined ? `${q.duration_seconds.toFixed(1)} s` : "?"}
      {q.net_speech_seconds !== undefined && <> · {q.net_speech_seconds.toFixed(1)} s of speech</>}
      {q.narrowband !== undefined && <> · {q.narrowband ? "narrowband (telephone-like)" : "wideband"} channel</>}
      {q.snr_db !== undefined && <> · SNR {q.snr_db.toFixed(0)} dB</>}
      {q.quality_score !== undefined && <> · heuristic quality {Math.round(q.quality_score)}/100</>}
    </p>
  );
}

function Disclaimer({ text }: { text: string }) {
  return <p className="disclaimer-banner" role="note"><strong>Investigative lead only.</strong> {text}</p>;
}

function StatusBanner({ status }: { status: VoiceprintStatus }) {
  if (status.available) {
    return (
      <p className="hint" style={{ marginBottom: 12 }}>
        {status.calibrated
          ? `Speaker engine ready · ${status.enrolled} voiceprints in your scope · decision threshold measured.`
          : `Speaker engine ready · ${status.enrolled} voiceprints in your scope · ranking only: no decision threshold has been measured yet.`}
      </p>
    );
  }
  return (
    <div className="info-banner" role="status" style={{ marginBottom: 14 }}>
      <strong>{status.enabled ? "Speaker identification is not available." : "Speaker identification is switched off."}</strong>{" "}
      {status.reason} The form below is shown so the workflow is visible; it is disabled and will work once the deployment has run the
      evaluation on real recordings and switched the feature on.
    </div>
  );
}

function MatchPanel({ status }: { status: VoiceprintStatus }) {
  const [ref, setRef] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [caseId, setCaseId] = useState("");
  const [justification, setJustification] = useState("");
  const [result, setResult] = useState<VoiceprintMatchResult | null>(null);
  const [refused, setRefused] = useState<{ message: string; quality?: VoiceprintQuality } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    setRefused(null);
    try {
      setResult(await voiceprintMatch(file, ref.trim(), caseId.trim() || undefined, justification.trim() || undefined));
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && err.detail?.quality_check) {
        setRefused({ message: String(err.detail.message ?? err.message), quality: err.detail.quality_check as VoiceprintQuality });
      } else {
        setError(err instanceof Error ? err.message : "Match failed.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="vp-match-h">
      <span className="section-label">01 · Match</span>
      <h2 id="vp-match-h">Match a recording against enrolled voices</h2>
      <form onSubmit={run}>
        <fieldset disabled={!status.available || busy} className="bare-fieldset">
          <AuthorizationField value={ref} onChange={setRef} />
          <AudioField file={file} onFile={(f) => { setFile(f); setResult(null); setRefused(null); }} disabled={!status.available} />
          <label>
            Case ID <span className="hint">(optional)</span>
            <input value={caseId} onChange={(e) => setCaseId(e.target.value)} placeholder="Attach this search to a case" />
          </label>
          {caseId.trim() && (
            <label>
              Justification <span className="hint">(required if the case is marked sensitive)</span>
              <input value={justification} onChange={(e) => setJustification(e.target.value)} />
            </label>
          )}
          <button disabled={!file || ref.trim().length < 3} style={{ width: "100%", justifyContent: "center" }}>{busy ? "Matching…" : "Run match"}</button>
        </fieldset>
        {busy && <><p className="hint" role="status" style={{ marginTop: 8 }}>Extracting the voiceprint and comparing it with enrolled voices…</p><Skeleton rows={3} /></>}
      </form>

      {error && <p className="error" role="alert" style={{ marginTop: 10 }}>{error}</p>}
      {refused && (
        <div style={{ marginTop: 12 }}>
          <div className="panel-error" role="alert">{refused.message} Nothing was matched.</div>
          {refused.quality && <Quality q={refused.quality} />}
        </div>
      )}

      {result && (
        <div className="hunt-result">
          <Quality q={result.quality_check} />
          {result.match_found === null && (
            <p className="hint" role="status"><strong>Ranking only.</strong> {result.note ?? "No decision threshold has been measured yet: candidates are ranked by raw score and a score must not be read as a match."}</p>
          )}
          {result.match_found === false && (
            <div className="hunt-match-badge not-found"><p style={{ margin: 0, fontWeight: 600, color: "var(--text-3)" }}>No enrolled voice reached the measured threshold</p></div>
          )}
          {result.candidates.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="audit-table" aria-label="Candidate voices">
                <thead><tr><th>#</th><th>Suspect</th><th>FIR</th><th>Raw score</th><th>Confidence</th></tr></thead>
                <tbody>
                  {result.candidates.map((c) => (
                    <tr key={`${c.rank}-${c.fir_id}`}>
                      <td>{c.rank}</td><td>{c.name}</td><td>{c.fir_id}</td>
                      <td style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>{c.score.toFixed(3)}</td>
                      <td>{c.confidence_label}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="hint">Authorization reference recorded: {result.lawful_interception_ref}</p>
        </div>
      )}
      {(result || refused) && <Disclaimer text={result?.label ?? FALLBACK_LABEL} />}
    </section>
  );
}

function EnrollPanel({ status }: { status: VoiceprintStatus }) {
  const [fields, setFields] = useState({ name: "", fir_id: "", lawful_interception_ref: "" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<VoiceprintEnrollResult | null>(null);
  const [error, setError] = useState("");

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setDone(null);
    try {
      setDone(await voiceprintEnroll({ ...fields, lawful_interception_ref: fields.lawful_interception_ref.trim() }, file));
      setFields({ name: "", fir_id: "", lawful_interception_ref: "" });
      setFile(null);
      (e.target as HTMLFormElement).reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Enrollment failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="vp-enroll-h">
      <span className="section-label">02 · Enroll</span>
      <h2 id="vp-enroll-h">Add a voiceprint to the index</h2>
      <form onSubmit={run}>
        <fieldset disabled={!status.available || busy} className="bare-fieldset">
          <div className="form-row">
            <label>Name <span style={{ color: "var(--red)" }} aria-hidden="true">*</span>
              <input value={fields.name} onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))} required maxLength={200} placeholder="Full name" />
            </label>
            <label>FIR ID <span style={{ color: "var(--red)" }} aria-hidden="true">*</span>
              <input value={fields.fir_id} onChange={(e) => setFields((f) => ({ ...f, fir_id: e.target.value }))} required maxLength={100} placeholder="FIR-2026-XXX" />
            </label>
          </div>
          <AuthorizationField value={fields.lawful_interception_ref} onChange={(v) => setFields((f) => ({ ...f, lawful_interception_ref: v }))} />
          <AudioField file={file} onFile={setFile} disabled={!status.available} />
          <button disabled={!file || fields.lawful_interception_ref.trim().length < 3} style={{ marginTop: 2 }}>{busy ? "Enrolling…" : "Enroll voiceprint"}</button>
        </fieldset>
        <p className="hint" style={{ marginTop: 8 }}>Only the voiceprint is kept; the recording itself is not stored.</p>
      </form>
      {error && <p className="error" role="alert" style={{ marginTop: 10 }}>{error}</p>}
      {done && (
        <p className="success-msg" role="status" style={{ marginTop: 10 }}>
          Enrolled {done.name} under {done.fir_id} ({done.net_speech_seconds.toFixed(1)} s of speech, heuristic quality {Math.round(done.channel_quality_score)}/100).
          Authorization {done.lawful_interception_ref} recorded. {done.label}
        </p>
      )}
    </section>
  );
}

function Panels(): ReactNode {
  const { user } = useAuth();
  const status = useAsync(voiceprintStatus);
  const canEnroll = user?.role === "supervisor" || user?.role === "admin"; // the API enforces the same rule
  if (status.loading && !status.data) return <section className="card"><Skeleton rows={4} /></section>;
  if (status.error || !status.data) {
    return (
      <div className="panel-error" role="alert">
        <span>Couldn't check whether speaker identification is available: {status.error}</span>
        <button type="button" className="link-btn" onClick={status.reload}>Retry</button>
      </div>
    );
  }
  return (
    <>
      <StatusBanner status={status.data} />
      <div className="grid-2">
        <ErrorBoundary label="the match panel"><MatchPanel status={status.data} /></ErrorBoundary>
        {canEnroll && <ErrorBoundary label="the enroll panel"><EnrollPanel status={status.data} /></ErrorBoundary>}
      </div>
    </>
  );
}

export default function VoiceprintHunt() {
  usePageTitle("Voice identification");
  return (
    <main>
      <PageHeader eyebrow="Biometric intelligence" title="Speaker Identification">
        Compare a recording with enrolled voiceprints. Only for audio obtained under lawful authorization: every search and enrolment
        records its authorization reference. Speaker identification is weaker evidence than a fingerprint.
      </PageHeader>
      <LeadNotice />
      <ErrorBoundary label="speaker identification"><Panels /></ErrorBoundary>
    </main>
  );
}
