import { FormEvent, useEffect, useState } from "react";
import PageHeader from "../components/PageHeader";
import QrCode from "../components/QrCode";
import RecoveryCodes from "../components/RecoveryCodes";
import { mfaDisable, mfaEnable, mfaSetup, mfaStatus } from "../lib/api";
import { useT } from "../i18n";

type Status = { enabled: boolean; required: boolean; recovery_codes_left: number };

export default function Security() {
  const t = useT();
  const [status, setStatus] = useState<Status | null>(null);
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = () => mfaStatus().then(setStatus).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load status."));
  useEffect(() => { void refresh(); }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed."); } finally { setBusy(false); }
  }

  const begin = () => run(async () => { const r = await mfaSetup(); setSetup({ secret: r.secret, uri: r.otpauth_uri }); setCode(""); });
  const enable = (e: FormEvent) => { e.preventDefault(); void run(async () => { const r = await mfaEnable(code.trim()); setCodes(r.recovery_codes); setSetup(null); setCode(""); await refresh(); }); };
  const disable = (e: FormEvent) => { e.preventDefault(); void run(async () => { await mfaDisable(code.trim()); setCode(""); setCodes(null); await refresh(); }); };

  return (
    <main>
      <PageHeader eyebrow={t("security.eyebrow")} title={t("security.title")}>
        Protect your account with a second step at sign-in, using any authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password…).
      </PageHeader>

      <section className="card" style={{ maxWidth: 560 }}>
        <span className="section-label">Two-step verification</span>
        {!status && !error && <p className="hint">Loading…</p>}
        {status && (
          <p style={{ margin: "6px 0 14px" }}>
            Status:{" "}
            <strong style={{ color: status.enabled ? "var(--green)" : "var(--amber)" }}>{status.enabled ? "On" : "Off"}</strong>
            {status.required && <span className="hint"> · required for your role</span>}
            {status.enabled && <span className="hint"> · {status.recovery_codes_left} recovery codes left</span>}
          </p>
        )}

        {codes && (
          <div style={{ marginBottom: 16 }}>
            <p className="lead-notice" role="status"><strong>Save these recovery codes now.</strong> They are shown once and each works a single time.</p>
            <RecoveryCodes codes={codes} />
          </div>
        )}

        {status && !status.enabled && !setup && <button onClick={() => void begin()} disabled={busy}>Set up two-step verification</button>}

        {setup && (
          <form onSubmit={enable}>
            <div className="mfa-enroll">
              <QrCode value={setup.uri} />
              <p className="hint">Scan with your authenticator app, or enter this key manually:</p>
              <code className="mfa-secret">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
            </div>
            <label>6-digit code
              <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" placeholder="123456" maxLength={7} autoFocus />
            </label>
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button disabled={busy || !code.trim()}>Turn on</button>
              <button type="button" className="secondary" onClick={() => setSetup(null)}>Cancel</button>
            </div>
          </form>
        )}

        {status?.enabled && !status.required && (
          <form onSubmit={disable} style={{ marginTop: 8 }}>
            <label>Enter a current code (or a recovery code) to turn it off
              <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" placeholder="123456" maxLength={16} />
            </label>
            <button className="secondary" disabled={busy || !code.trim()} style={{ marginTop: 10 }}>Turn off</button>
          </form>
        )}

        {error && <p className="error" role="alert" style={{ marginTop: 12 }}>{error}</p>}
      </section>
    </main>
  );
}
