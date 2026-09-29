import { FormEvent, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import QrCode from "../components/QrCode";
import RecoveryCodes from "../components/RecoveryCodes";
import { mfaEnrollBegin, mfaEnrollComplete, mfaVerify } from "../lib/api";
import type { MfaSession } from "../lib/api";
import { useAuth } from "../lib/auth";
import { usePageTitle } from "../lib/usePageTitle";

// Demo accounts are hidden unless explicitly enabled (VITE_SHOW_DEMO_ACCOUNTS=true), even in dev,
// and are never included in production builds.
const DEMO_ACCOUNTS =
  import.meta.env.DEV && import.meta.env.VITE_SHOW_DEMO_ACCOUNTS === "true"
    ? [
        { id: "INV001",         password: "demo123",     role: "investigator", name: "Rahul Verma" },
        { id: "ANL001",         password: "demo123",     role: "analyst",      name: "Ayesha Khan" },
        { id: "SUP001",         password: "demo123",     role: "supervisor",   name: "D. Iyer" },
        { id: "admin@demo.com", password: "password123", role: "admin",        name: "System Admin" },
      ]
    : [];

type Stage =
  | { name: "password" }
  | { name: "code"; mfaToken: string }
  | { name: "enroll"; mfaToken: string; secret: string; uri: string }
  | { name: "recovery"; session: MfaSession };

export default function Login() {
  const { user, login, completeSession } = useAuth();
  usePageTitle("Sign in");
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const sessionExpired = searchParams.get("expired") === "1";
  const [employeeId, setEmployeeId] = useState("");
  const [password, setPassword]     = useState("");
  const [error, setError]           = useState("");
  const [busy, setBusy]             = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [capsLock, setCapsLock]     = useState(false);
  const [stage, setStage]           = useState<Stage>({ name: "password" });
  const [code, setCode]             = useState("");
  const [useRecovery, setUseRecovery] = useState(false);

  // Where to go after sign-in: ?next= (set when a session expires) or the page that sent us here.
  // Only same-site paths are accepted, so this can't be used as an open redirect.
  const requested = searchParams.get("next") ?? (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? "/";
  const destination = requested.startsWith("/") && !requested.startsWith("//") && !requested.startsWith("/login") ? requested : "/";

  if (user && stage.name !== "recovery") return <Navigate to={destination} replace />;

  function fail(err: unknown, fallback = "Authentication failed.") {
    setError(err instanceof Error ? err.message : fallback);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!employeeId.trim() || !password) {
      setError("Enter your Employee ID and password.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const pending = await login(employeeId.trim(), password);
      if (!pending) {
        navigate(destination, { replace: true });
      } else if (pending.enrollment) {
        const begin = await mfaEnrollBegin(pending.mfaToken);
        setStage({ name: "enroll", mfaToken: pending.mfaToken, secret: begin.secret, uri: begin.otpauth_uri });
      } else {
        setStage({ name: "code", mfaToken: pending.mfaToken });
      }
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    if (stage.name !== "code" && stage.name !== "enroll") return;
    if (!code.trim()) { setError(useRecovery ? "Enter a recovery code." : "Enter the 6-digit code."); return; }
    setBusy(true);
    setError("");
    try {
      if (stage.name === "code") {
        const session = await mfaVerify(stage.mfaToken, code.trim());
        completeSession(session.token, session.user);
        navigate(destination, { replace: true });
      } else {
        const session = await mfaEnrollComplete(stage.mfaToken, code.trim());
        setStage({ name: "recovery", session });
      }
      setCode("");
    } catch (err) {
      fail(err, "Verification failed.");
    } finally {
      setBusy(false);
    }
  }

  function backToPassword() {
    setStage({ name: "password" });
    setCode("");
    setError("");
    setUseRecovery(false);
  }

  return (
    <div className="login-page">
      <div className="login-top">
        <Link to="/" className="home-brand" aria-label="ARGUS home">
          <span className="home-logo" aria-hidden="true" />
          <span>ARGUS</span>
        </Link>
        <Link to="/" className="login-back">← Back to home</Link>
      </div>

      <main className="login-main">
        <div className="login-card">
          <div className="login-card-header">
            <span className="eyebrow">{stage.name === "password" ? "Secure sign-in" : "Two-step verification"}</span>
            <h1>{stage.name === "password" ? "Access ARGUS" : stage.name === "enroll" ? "Set up your authenticator" : stage.name === "recovery" ? "Save your recovery codes" : "Enter your code"}</h1>
            <p>{stage.name === "password" ? "Use your NCRB Employee ID and department password."
              : stage.name === "code" ? (useRecovery ? "Enter one of your saved recovery codes." : "Open your authenticator app and enter the 6-digit code for ARGUS.")
              : stage.name === "enroll" ? "Your role requires two-step verification. Scan the code with an authenticator app, then enter the 6-digit code it shows."
              : "Each code works once. Store them somewhere safe — they are the only way in if you lose your phone."}</p>
          </div>

          {sessionExpired && !error && stage.name === "password" && (
            <p className="login-notice warn" role="status">Your session has expired. Please sign in again.</p>
          )}

          {stage.name === "password" && (
          <form onSubmit={submit} noValidate>
            <label>
              Employee ID
              <input
                value={employeeId}
                onChange={(e) => setEmployeeId(e.target.value)}
                placeholder="e.g. INV001"
                autoFocus
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                disabled={busy}
              />
            </label>
            <label>
              Password
              <span className="password-field">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyUp={(e) => setCapsLock(e.getModifierState("CapsLock"))}
                  onBlur={() => setCapsLock(false)}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                  disabled={busy}
                />
                <button
                  type="button"
                  className="password-toggle"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </span>
            </label>
            {capsLock && <p className="login-hint" role="status">Caps Lock is on.</p>}

            <div aria-live="polite">
              {error && <p className="login-notice error" role="alert">{error}</p>}
            </div>

            <button type="submit" className="login-submit" disabled={busy}>
              {busy
                ? <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                    <span className="spinner" /> Authenticating…
                  </span>
                : "Sign in →"}
            </button>
          </form>
          )}

          {(stage.name === "code" || stage.name === "enroll") && (
            <form onSubmit={submitCode} noValidate>
              {stage.name === "enroll" && (
                <div className="mfa-enroll">
                  <QrCode value={stage.uri} />
                  <p className="hint" style={{ margin: 0 }}>Can't scan? Enter this key manually:</p>
                  <code className="mfa-secret">{stage.secret.match(/.{1,4}/g)?.join(" ")}</code>
                </div>
              )}
              <label>
                {useRecovery ? "Recovery code" : "6-digit code"}
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  inputMode={useRecovery ? "text" : "numeric"}
                  autoComplete="one-time-code"
                  placeholder={useRecovery ? "XXXXX-XXXXX" : "123456"}
                  maxLength={useRecovery ? 16 : 7}
                  autoFocus
                  disabled={busy}
                />
              </label>
              <div aria-live="polite">{error && <p className="login-notice error" role="alert">{error}</p>}</div>
              <button type="submit" className="login-submit" disabled={busy}>
                {busy ? <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}><span className="spinner" /> Verifying…</span> : stage.name === "enroll" ? "Verify & continue →" : "Verify →"}
              </button>
              <div className="login-links">
                {stage.name === "code" && (
                  <button type="button" className="link-btn" onClick={() => { setUseRecovery((v) => !v); setCode(""); setError(""); }}>
                    {useRecovery ? "Use authenticator code" : "Use a recovery code"}
                  </button>
                )}
                <button type="button" className="link-btn" onClick={backToPassword}>← Different account</button>
              </div>
            </form>
          )}

          {stage.name === "recovery" && (
            <div>
              <RecoveryCodes codes={stage.session.recoveryCodes ?? []} />
              <button
                type="button"
                className="login-submit"
                style={{ marginTop: 16 }}
                onClick={() => { completeSession(stage.session.token, stage.session.user); navigate(destination, { replace: true }); }}
              >
                I've saved my codes — continue →
              </button>
            </div>
          )}

          {/* Demo accounts — only visible in dev mode */}
          {stage.name === "password" && DEMO_ACCOUNTS.length > 0 && (
            <>
              <div className="login-divider">Demo accounts</div>
              <div className="demo-accounts">
                {DEMO_ACCOUNTS.map((a) => (
                  <button
                    key={a.id}
                    type="button"
                    className="demo-account-btn"
                    onClick={() => { setEmployeeId(a.id); setPassword(a.password); setError(""); }}
                  >
                    <div style={{ textAlign: "left" }}>
                      <div style={{ fontWeight: 700, fontSize: "0.875rem", color: "var(--text)" }}>{a.name}</div>
                      <div style={{ fontSize: "0.75rem", color: "var(--text-2)", marginTop: 1 }}>{a.id}</div>
                    </div>
                    <span className={`role-badge role-${a.role}`}>{a.role}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        <p className="login-legal">
          Authorised personnel only. Access is logged in a tamper-evident audit trail. Unauthorised access is an offence under the IT Act, 2000.
        </p>
      </main>
    </div>
  );
}
