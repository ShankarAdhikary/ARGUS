import { ChangeEvent, FormEvent, useState } from "react";
import ErrorBoundary from "../components/ErrorBoundary";
import LeadNotice from "../components/LeadNotice";
import PageHeader from "../components/PageHeader";
import Skeleton from "../components/Skeleton";
import { fingerprintEnroll, fingerprintMatch, fingerprintStatus } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAsync } from "../lib/useAsync";
import { usePageTitle } from "../lib/usePageTitle";
import { ApiError } from "../lib/api";
import type { FingerprintMatchResult, FingerprintPrintType, FingerprintQualityCheck } from "../types";
import { TKey, useT } from "../i18n";

/** Same bands the API uses: 70+ High, 40-69 Medium, below that Low / insufficient. */
function tierClass(label: string): string {
  return label.startsWith("High") ? "pill-high" : label.startsWith("Medium") ? "pill-medium" : "pill-low";
}

/** The API's band text ("High", "Medium", "Low / insufficient") shown in the UI language. */
function bandLabel(label: string, t: (k: TKey) => string): string {
  if (label.startsWith("High")) return t("fingerprint.confidence_high");
  if (label.startsWith("Medium")) return t("fingerprint.confidence_medium");
  return t("fingerprint.confidence_low");
}

function EngineBanner() {
  const t = useT();
  const status = useAsync(fingerprintStatus);
  if (status.loading && !status.data) return <Skeleton rows={1} />;
  if (status.error) {
    return (
      <div className="panel-error" role="alert">
        <span>Couldn't check the fingerprint engine: {status.error}</span>
        <button type="button" className="link-btn" onClick={status.reload}>{t("common.retry")}</button>
      </div>
    );
  }
  if (!status.data || status.data.available) {
    return status.data ? <p className="hint">Engine: {status.data.engine} · {status.data.enrolled} prints enrolled in your scope · match threshold {status.data.match_threshold}</p> : null;
  }
  return (
    <div className="panel-error" role="alert" style={{ marginBottom: 14 }}>
      <span><strong>{t("fingerprint.status_unavailable")}</strong> {status.data.message}</span>
    </div>
  );
}

function MatchPanel() {
  const t = useT();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [printType, setPrintType] = useState<FingerprintPrintType>("rolled");
  const [result, setResult] = useState<FingerprintMatchResult | null>(null);
  const [refused, setRefused] = useState<FingerprintQualityCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setResult(null);
    setRefused(null);
    setError("");
    setPreview(f ? URL.createObjectURL(f) : null);
  }

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    setRefused(null);
    try {
      setResult(await fingerprintMatch(file, printType));
    } catch (err) {
      // A print that fails the quality gate is refused with 422 before any matching; show the reading, not just the text.
      if (err instanceof ApiError && err.status === 422 && err.detail?.quality_check) setRefused(err.detail.quality_check as FingerprintQualityCheck);
      setError(err instanceof Error ? err.message : "Match failed.");
    } finally {
      setBusy(false);
    }
  }

  const q = result?.quality_check ?? refused;
  return (
    <section className="card">
      <span className="section-label">01 · {t("fingerprint.match_panel")}</span>
      <h2>{t("fingerprint.match_heading")}</h2>
      <form onSubmit={run}>
        <label className="hunt-dropzone" htmlFor="fp-match-input" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, cursor: "pointer" }}>
          {preview ? (
            <img src={preview} alt="Selected fingerprint" style={{ width: 160, height: 160, objectFit: "contain", borderRadius: "var(--radius-md)", border: "1px solid var(--border-hot)" }} />
          ) : (
            <>
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-3)" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
                <path d="M12 3a6 6 0 0 0-6 6v3M12 7a2 2 0 0 0-2 2v6M12 11v6M16 9a4 4 0 0 0-8 0M16 13v3a6 6 0 0 1-2 4.5M8 14v1a6 6 0 0 0 1.5 4" />
              </svg>
              <span style={{ fontWeight: 600, color: "var(--text-2)", fontSize: "0.875rem" }}>{t("fingerprint.upload_prompt")}</span>
              <span className="hint">{t("fingerprint.upload_hint")}</span>
            </>
          )}
          <input id="fp-match-input" type="file" accept="image/*" onChange={onFile} style={{ display: "none" }} />
        </label>
        <fieldset className="bare-fieldset" style={{ margin: "10px 0" }}>
          <legend className="hint">Type of print</legend>
          <label style={{ display: "inline-flex", gap: 6, marginRight: 16 }}>
            <input type="radio" name="fp-type" checked={printType === "rolled"} onChange={() => setPrintType("rolled")} /> Rolled / plain print
          </label>
          <label style={{ display: "inline-flex", gap: 6 }}>
            <input type="radio" name="fp-type" checked={printType === "latent"} onChange={() => setPrintType("latent")} /> {t("fingerprint.latent_flag")}
          </label>
        </fieldset>
        <button disabled={busy || !file} style={{ width: "100%", justifyContent: "center" }}>{busy ? t("fingerprint.matching") : t("fingerprint.run_match")}</button>
        {busy && <><p className="hint" role="status" style={{ marginTop: 8 }}>Extracting minutiae and comparing against enrolled prints — this can take up to a minute.</p><Skeleton rows={3} /></>}
      </form>

      {error && <p className="error" role="alert" style={{ marginTop: 10 }}>{error}</p>}

      {(result || refused) && q && (
        <div className="hunt-result">
          <div className="wsrs-bar-row" style={{ marginBottom: 12 }}>
            <div className="wsrs-bar-head">
              <span>Print quality <span className="hint">· {q.minutiae ?? "?"} minutiae found (minimum {q.minimum_minutiae ?? "?"})</span></span>
              <span className="wsrs-bar-score">{Math.round(q.quality_score)}</span>
            </div>
            <div className="wsrs-bar-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(q.quality_score)} aria-label="Print quality">
              <div className="wsrs-bar-fill" style={{ width: `${Math.min(q.quality_score, 100)}%`, background: q.passed ? "var(--green)" : "var(--red)" }} />
            </div>
            <span className="hint">{q.method ? `Measured by ${q.method.replace(/\+/g, " + ")}` : "Quality reading"} — this is not an NFIQ score{q.valid_blocks !== undefined ? ` (${q.valid_blocks} clear ridge blocks)` : ""}.</span>
          </div>

          {!q.passed && <div className="panel-error" role="alert">{t("fingerprint.quality_failed")} Nothing was matched. Try a better impression or a clearer image.</div>}

          {result && q.passed && !result.match_found && (
            <div className="hunt-match-badge not-found"><p style={{ margin: 0, fontWeight: 600, color: "var(--text-3)" }}>{t("fingerprint.no_match")}</p></div>
          )}

          {result?.match_found && (
            <>
              <ol className="geo-forecast-list" aria-label="Candidate matches">
                {result.candidates.map((c) => (
                  <li key={`${c.rank}-${c.fir_id}`}>
                    <div className="wsrs-bar-head">
                      <strong>#{c.rank} {c.name}</strong>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
                        {/* The raw SourceAFIS score is the number an examiner reads; the label is only a band over it. */}
                        <span className="wsrs-bar-score" style={{ fontSize: "1.125rem" }} title={t("fingerprint.score_label")}>
                          {c.score.toFixed(1)}
                        </span>
                        <span className={`pill ${tierClass(c.confidence_label)}`}><span className="pill-dot" /> {bandLabel(c.confidence_label, t)}</span>
                      </span>
                    </div>
                    <span className="hint">{c.fir_id} · SourceAFIS raw score {c.score.toFixed(1)}</span>
                  </li>
                ))}
              </ol>
              <p className="hint" style={{ marginTop: 8 }}>
                Scores are raw SourceAFIS values, not percentages: they can exceed 100, and 40 is the match threshold. Bands: 70+ High, 40–69 Medium.
                They are not comparable with scores from other AFIS products.
              </p>
            </>
          )}

          {result && <p className="lead-notice" role="note" style={{ marginTop: 12 }}>{result.disclaimer}</p>}
        </div>
      )}
    </section>
  );
}

function EnrollPanel() {
  const t = useT();
  const [fields, setFields] = useState({ name: "", fir_id: "" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setStatus(null);
    try {
      const r = await fingerprintEnroll(fields, file);
      setStatus({ ok: true, text: `Enrolled ${r.name} under ${r.fir_id}. ${r.message}` });
      setFields({ name: "", fir_id: "" });
      setFile(null);
      (e.target as HTMLFormElement).reset();
    } catch (err) {
      setStatus({ ok: false, text: err instanceof Error ? err.message : "Enrollment failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <span className="section-label">02 · {t("fingerprint.enroll_panel")}</span>
      <h2>{t("fingerprint.enroll_heading")}</h2>
      <form onSubmit={run}>
        <div className="form-row">
          <label>{t("fingerprint.name")} *<input value={fields.name} onChange={(e) => setFields((f) => ({ ...f, name: e.target.value }))} required maxLength={200} placeholder="Full name" /></label>
          <label>{t("hunt.fir_id")} *<input value={fields.fir_id} onChange={(e) => setFields((f) => ({ ...f, fir_id: e.target.value }))} required maxLength={100} placeholder="FIR-2026-XXX" /></label>
        </div>
        <label>Fingerprint image *<input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required /></label>
        <button disabled={busy || !file} style={{ marginTop: 2 }}>{busy ? t("fingerprint.enrolling") : t("fingerprint.enroll_button")}</button>
        {busy && <p className="hint" role="status" style={{ marginTop: 8 }}>Extracting minutiae and updating the index…</p>}
      </form>
      {status && <p className={status.ok ? "success-msg" : "error"} role={status.ok ? "status" : "alert"} style={{ marginTop: 10 }}>{status.text}</p>}
    </section>
  );
}

export default function FingerprintHunt() {
  const t = useT();
  usePageTitle(t("fingerprint.title"));
  const { user } = useAuth();
  const canEnroll = user?.role === "supervisor" || user?.role === "admin"; // the API enforces the same rule
  return (
    <main>
      <PageHeader eyebrow={t("fingerprint.eyebrow")} title={t("fingerprint.title")}>
        {t("fingerprint.subtitle")}
      </PageHeader>
      <LeadNotice>{t("lead_notice.fingerprint")}</LeadNotice>
      <ErrorBoundary label="the engine status"><EngineBanner /></ErrorBoundary>
      <div className="grid-2">
        <ErrorBoundary label="the match panel"><MatchPanel /></ErrorBoundary>
        {canEnroll && <ErrorBoundary label="the enroll panel"><EnrollPanel /></ErrorBoundary>}
      </div>
    </main>
  );
}
