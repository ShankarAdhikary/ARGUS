import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import { createAlertRule, entityTypeGuess, listAlertRules, listAlerts, markAlertRead, markAllAlertsRead } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { AlertRecord, AlertRule } from "../types";

export default function Alerts() {
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [entityValue, setEntityValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<"unread" | "all">("unread");
  const [createStatus, setCreateStatus] = useState("");

  function refresh() {
    listAlerts().then(setAlerts).catch(() => {});
    listAlertRules().then(setRules).catch(() => {});
  }

  useEffect(refresh, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!entityValue.trim()) return;
    setBusy(true);
    setCreateStatus("");
    try {
      await createAlertRule(entityValue.trim());
      setEntityValue("");
      setCreateStatus("Watch rule created successfully.");
      refresh();
    } catch (err) {
      setCreateStatus(err instanceof Error ? err.message : "Failed to create rule.");
    } finally {
      setBusy(false);
    }
  }

  const unread = alerts.filter((a) => !a.read_status);
  const visible = filter === "unread" ? unread : alerts;

  async function readOne(id: string) {
    setAlerts((prev) => prev.map((a) => (a.alert_id === id ? { ...a, read_status: true } : a)));
    await markAlertRead(id).catch(refresh);
  }
  async function readAll() {
    setAlerts((prev) => prev.map((a) => ({ ...a, read_status: true })));
    await markAllAlertsRead().catch(refresh);
  }

  return (
    <main>
      <PageHeader eyebrow="Watchlists" title="Alerts">
        Get notified when a watched entity appears in new records. {unread.length > 0 && (
          <strong style={{ color: "var(--amber)" }}>{unread.length} unread alert{unread.length === 1 ? "" : "s"}.</strong>
        )}
      </PageHeader>

      <div className="grid-2">
        {/* Triggered alerts */}
        <section className="card">
          <div className="card-title">
            <h2 className="card-heading">Triggered alerts</h2>
            {unread.length > 0
              ? <button type="button" className="link-btn" onClick={() => void readAll()}>Mark all {unread.length} as read</button>
              : <span className="hint">{alerts.length} total</span>}
          </div>
          <div className="filter-row" role="tablist" aria-label="Filter alerts">
            {(["unread", "all"] as const).map((f) => (
              <button key={f} type="button" role="tab" aria-selected={filter === f} className={`chip ${filter === f ? "chip-active" : ""}`} onClick={() => setFilter(f)}>
                {f === "unread" ? `Unread (${unread.length})` : `All (${alerts.length})`}
              </button>
            ))}
          </div>
          <div className="alert-list">
            {visible.map((a) => (
              <div key={a.alert_id} className={`alert-row alert-row-split ${!a.read_status ? "alert-unread" : ""}`}>
                <span className={`dot ${a.read_status ? "" : "warn"}`} style={{ marginTop: 6, flexShrink: 0 }} aria-hidden="true" />
                <Link to={`/search?q=${encodeURIComponent(a.entity_value)}`} className="alert-row-body" style={{ flex: 1, textDecoration: "none" }}>
                  <p className="alert-row-msg">{a.message}</p>
                  <span className="hint" title={new Date(a.triggered_at).toLocaleString("en-IN")}>{relativeTime(a.triggered_at)}</span>
                </Link>
                {!a.read_status && (
                  <button type="button" className="link-btn" onClick={() => void readOne(a.alert_id)} aria-label={`Mark alert about ${a.entity_value} as read`}>Mark read</button>
                )}
              </div>
            ))}
            {!visible.length && (
              <p className="hint">{filter === "unread" && alerts.length ? "You're all caught up." : "No alerts triggered yet. Watch an entity to get notified when it appears in new records."}</p>
            )}
          </div>
        </section>

        {/* Watch rules */}
        <section className="card">
          <div className="card-title">
            <h2 className="card-heading">Watch rules</h2>
            <span className="hint">{rules.length} active</span>
          </div>

          <form onSubmit={submit} style={{ marginBottom: 16 }}>
            <label>
              Watch an entity
              <input
                placeholder="Name, phone number, or account ID"
                value={entityValue}
                onChange={(e) => setEntityValue(e.target.value)}
              />
            </label>
            <button disabled={busy}>{busy ? "Saving…" : "Create watch rule"}</button>
          </form>

          {createStatus && (
            <p className={createStatus.includes("Failed") ? "error" : "success-msg"} style={{ marginBottom: 12 }}>
              {createStatus}
            </p>
          )}

          <div className="case-list">
            {rules.map((r) => (
              <div key={r.rule_id} className="case-row" style={{ cursor: "default" }}>
                <span className={`type-dot type-${entityTypeGuess(r.entity_value)}`} />
                <div className="case-row-info">
                  <span className="case-row-title">{r.entity_value}</span>
                  <span className="case-row-meta">Since {new Date(r.created_at).toLocaleDateString("en-IN")}</span>
                </div>
                <Link to={`/search?q=${encodeURIComponent(r.entity_value)}`} className="btn-link" style={{ fontSize: "0.8125rem" }}>Search →</Link>
              </div>
            ))}
            {!rules.length && <p className="hint">No active watch rules.</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
