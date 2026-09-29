import { ChangeEvent, FormEvent, useState } from "react";
import PageHeader from "../components/PageHeader";
import ConfidencePill from "../components/ConfidencePill";
import { biometricHunt, biometricUnifiedEnroll } from "../lib/api";

export default function Hunt() {
  const [huntFile, setHuntFile] = useState<File | null>(null);
  const [huntPreview, setHuntPreview] = useState<string | null>(null);
  const [huntResult, setHuntResult] = useState<{ match_found: boolean; confidence_score?: number; suspect_data?: Record<string, unknown> } | null>(null);
  const [huntBusy, setHuntBusy] = useState(false);
  const [huntError, setHuntError] = useState("");

  const [enrollFile, setEnrollFile] = useState<File | null>(null);
  const [fields, setFields] = useState({ fir_id: "", accused: "", mobile: "", aadhaar: "", dob: "", history: "", prison: "" });
  const [enrollBusy, setEnrollBusy] = useState(false);
  const [enrollStatus, setEnrollStatus] = useState("");

  function onHuntFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    setHuntFile(f);
    setHuntResult(null);
    if (f) setHuntPreview(URL.createObjectURL(f));
    else setHuntPreview(null);
  }

  async function runHunt(event: FormEvent) {
    event.preventDefault();
    if (!huntFile) return;
    setHuntBusy(true);
    setHuntError("");
    setHuntResult(null);
    try {
      const result = await biometricHunt(huntFile);
      setHuntResult(result);
    } catch (err) {
      setHuntError(err instanceof Error ? err.message : "Hunt failed.");
    } finally {
      setHuntBusy(false);
    }
  }

  async function runEnroll(event: FormEvent) {
    event.preventDefault();
    if (!enrollFile) return;
    setEnrollBusy(true);
    setEnrollStatus("");
    try {
      await biometricUnifiedEnroll(fields, enrollFile);
      setEnrollStatus("Profile enrolled across face index, search index, and graph.");
      setFields({ fir_id: "", accused: "", mobile: "", aadhaar: "", dob: "", history: "", prison: "" });
      setEnrollFile(null);
    } catch (err) {
      setEnrollStatus(err instanceof Error ? err.message : "Enrollment failed.");
    } finally {
      setEnrollBusy(false);
    }
  }

  function onField(key: keyof typeof fields) {
    return (e: ChangeEvent<HTMLInputElement>) => setFields((prev) => ({ ...prev, [key]: e.target.value }));
  }

  return (
    <main>
      <PageHeader eyebrow="Biometric intelligence" title="Facial Recognition Hunt">
        ArcFace + RetinaFace embeddings matched against a FAISS index. A match is an investigative lead — always verify before action.
      </PageHeader>

      <div className="grid-2">
        {/* Hunt panel */}
        <section className="card">
          <span className="section-label">01 · Hunt</span>
          <h2>Match photo against enrolled suspects</h2>
          <form onSubmit={runHunt}>
            <label
              className="hunt-dropzone"
              htmlFor="hunt-file-input"
              style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, cursor: "pointer" }}
            >
              {huntPreview ? (
                <img src={huntPreview} alt="Selected" style={{ width: 160, height: 160, objectFit: "cover", borderRadius: "var(--radius-md)", border: "1px solid var(--border-hot)" }} />
              ) : (
                <>
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-3)" strokeWidth="1.5" strokeLinecap="round">
                    <rect x="3" y="3" width="18" height="18" rx="3"/>
                    <circle cx="9" cy="9" r="2"/>
                    <path d="M21 15l-5-5L5 21"/>
                  </svg>
                  <span style={{ fontWeight: 600, color: "var(--text-2)", fontSize: "0.875rem" }}>Click to upload a photo</span>
                  <span className="hint">JPEG, PNG — suspect photo for matching</span>
                </>
              )}
              <input id="hunt-file-input" type="file" accept="image/*" onChange={onHuntFile} style={{ display: "none" }} />
            </label>
            <button disabled={huntBusy || !huntFile} style={{ width: "100%", justifyContent: "center" }}>
              {huntBusy ? "Matching…" : "Run hunt"}
            </button>
            {huntBusy && <p className="hint" role="status" style={{ marginTop: 8 }}>Comparing against enrolled faces — this usually takes 5–15 seconds, longer right after a restart.</p>}
          </form>

          {huntError && <p className="error" style={{ marginTop: 10 }}>{huntError}</p>}

          {huntResult && (
            <div className="hunt-result">
              <div className={`hunt-match-badge ${huntResult.match_found ? "found" : "not-found"}`}>
                {huntResult.match_found ? (
                  <>
                    <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="var(--green)" strokeWidth="1.8" strokeLinecap="round"><path d="M2 8l4 4 8-8"/></svg>
                    <div>
                      <p style={{ margin: 0, fontWeight: 800, color: "var(--green)" }}>Match found</p>
                      <ConfidencePill confidence={(huntResult.confidence_score ?? 0) / 100} />
                    </div>
                  </>
                ) : (
                  <>
                    <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="var(--text-3)" strokeWidth="1.8" strokeLinecap="round"><path d="M12 4L4 12M4 4l8 8"/></svg>
                    <p style={{ margin: 0, fontWeight: 600, color: "var(--text-3)" }}>No match above confidence threshold</p>
                  </>
                )}
              </div>
              {huntResult.match_found && huntResult.suspect_data && (
                <dl className="kv-grid">
                  {Object.entries(huntResult.suspect_data).map(([k, v]) => (
                    <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></div>
                  ))}
                </dl>
              )}
            </div>
          )}
        </section>

        {/* Enroll panel */}
        <section className="card">
          <span className="section-label">02 · Unified enroll</span>
          <h2>Add a suspect profile</h2>
          <form onSubmit={runEnroll}>
            <div className="form-row">
              <label>FIR ID *<input value={fields.fir_id} onChange={onField("fir_id")} required placeholder="FIR-2026-XXX" /></label>
              <label>Accused name *<input value={fields.accused} onChange={onField("accused")} required placeholder="Full name" /></label>
            </div>
            <div className="form-row">
              <label>Mobile<input value={fields.mobile} onChange={onField("mobile")} placeholder="+91 xxxxx xxxxx" /></label>
              <label>Aadhaar (tokenized)<input value={fields.aadhaar} onChange={onField("aadhaar")} placeholder="XXXX XXXX XXXX" /></label>
            </div>
            <div className="form-row">
              <label>Date of birth<input type="date" value={fields.dob} onChange={onField("dob")} /></label>
              <label>Prison facility<input value={fields.prison} onChange={onField("prison")} placeholder="e.g. Tihar Jail" /></label>
            </div>
            <label>Criminal history<input value={fields.history} onChange={onField("history")} placeholder="Brief summary of prior offences" /></label>
            <label>
              Enrollment photo *
              <input type="file" accept="image/*" onChange={(e) => setEnrollFile(e.target.files?.[0] ?? null)} required />
            </label>
            <button disabled={enrollBusy} style={{ marginTop: 2 }}>{enrollBusy ? "Enrolling…" : "Enroll suspect"}</button>
            {enrollBusy && <p className="hint" role="status" style={{ marginTop: 8 }}>Building the face profile — usually 5–15 seconds.</p>}
          </form>
          {enrollStatus && (
            <p className={enrollStatus.includes("failed") ? "error" : "success-msg"} style={{ marginTop: 10 }}>{enrollStatus}</p>
          )}
        </section>
      </div>
    </main>
  );
}
