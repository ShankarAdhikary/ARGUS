import { FormEvent, ReactNode, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import {
  centrality,
  dashboardSummary,
  health,
  listAlerts,
  listCases,
  listPatterns,
  markAllAlertsRead,
  repeatVictims,
  wsrsLeaderboard,
} from "../lib/api";
import { formatCount, relativeTime } from "../lib/format";
import { clearRecentSearches, getRecentSearches, rememberSearch } from "../lib/recent";
import { useAsync } from "../lib/useAsync";
import { usePageTitle } from "../lib/usePageTitle";
import WsrsBadge from "../components/WsrsBadge";
import ConfidencePill from "../components/ConfidencePill";
import WomenSafetyBadge from "../components/WomenSafetyBadge";
import type { AlertRecord } from "../types";
import { useT } from "../i18n";

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/** Repeated firings of the same watch rule are shown once, with a count. */
function groupAlerts(alerts: AlertRecord[]) {
  const groups = new Map<string, { latest: AlertRecord; count: number; unread: number }>();
  for (const a of alerts) {
    const g = groups.get(a.message);
    if (!g) groups.set(a.message, { latest: a, count: 1, unread: a.read_status ? 0 : 1 });
    else {
      g.count += 1;
      g.unread += a.read_status ? 0 : 1;
      if (a.triggered_at > g.latest.triggered_at) g.latest = a;
    }
  }
  return [...groups.values()].sort((x, y) => (y.unread > 0 ? 1 : 0) - (x.unread > 0 ? 1 : 0) || y.latest.triggered_at.localeCompare(x.latest.triggered_at));
}

export default function Dashboard() {
  const { user } = useAuth();
  const t = useT();
  usePageTitle(t("nav.dashboard"));
  const navigate = useNavigate();
  const cases = useAsync(listCases);
  const alerts = useAsync(listAlerts);
  const patterns = useAsync(listPatterns);
  const summary = useAsync(dashboardSummary);
  const influencers = useAsync(centrality);
  const showSystem = user?.role === "admin" || user?.role === "supervisor";
  const canIngest = user?.role !== "analyst";
  const system = useAsync(() => (showSystem ? health() : Promise.resolve(null)));
  // Victim analytics are supervisor/admin only (the API enforces it too); nobody else even requests them.
  const highRisk = useAsync(() => (showSystem ? wsrsLeaderboard(5) : Promise.resolve(null)));
  const victims = useAsync(() => (showSystem ? repeatVictims() : Promise.resolve(null)));

  const [query, setQuery] = useState("");
  const [recent, setRecent] = useState(getRecentSearches);

  function go(q: string) {
    const term = q.trim();
    if (!term) return;
    rememberSearch(term);
    setRecent(getRecentSearches());
    navigate(`/search?q=${encodeURIComponent(term)}`);
  }
  function submitSearch(e: FormEvent) {
    e.preventDefault();
    go(query);
  }

  const openCases = (cases.data ?? []).filter((c) => c.status !== "closed");
  const sensitiveCases = (cases.data ?? []).filter((c) => c.is_sensitive);
  const unread = (alerts.data ?? []).filter((a) => !a.read_status);
  const strongPatterns = useMemo(
    () => (patterns.data ?? []).filter((p) => p.status === "new" && p.confidence >= 0.75).sort((a, b) => b.confidence - a.confidence),
    [patterns.data]
  );
  const alertGroups = useMemo(() => groupAlerts(alerts.data ?? []), [alerts.data]);
  const topInfluencers = (influencers.data ?? []).slice(0, 6);
  const firstName = user?.full_name.split(" ")[0] ?? "";
  const nothingYet = !cases.loading && !cases.error && (cases.data ?? []).length === 0;
  const allUp = system.data ? Object.values(system.data.dependencies).every(Boolean) : true;

  async function readAll() {
    await markAllAlertsRead().catch(() => undefined);
    alerts.reload();
  }

  return (
    <main>
      {/* ── Header ── */}
      <header className="page-header">
        <div className="page-header-left">
          <span className="eyebrow">{user?.role} · {user?.jurisdiction}</span>
          <h1>{greeting()}, {firstName}</h1>
          <p className="page-subtitle">
            {new Date().toLocaleDateString("en-IN", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
            {showSystem && system.data && (
              <span className="system-chip" title="Live status of the platform services">
                <span className={`dot ${allUp ? "ok" : "alert"}`} />
                {allUp ? "All systems operational" : "Service degraded"}
              </span>
            )}
          </p>
        </div>
        <div className="page-actions">
          <Link to="/cases?new=1" className="btn-link primary-link">+ New case</Link>
          {canIngest && <Link to="/ingestion" className="secondary btn-link">Ingest data</Link>}
        </div>
      </header>

      {/* ── Overview: every tile is a shortcut ── */}
      <div className="stat-row" role="list" aria-label="Overview">
        <StatTile to="/cases" label={t("dashboard.open_cases")} value={cases.loading ? null : openCases.length} hint="View all cases" />
        <StatTile to="/alerts" label={t("dashboard.unread_alerts")} value={alerts.loading ? null : unread.length} hint={unread.length ? "Review now" : "All caught up"} tone={unread.length ? "warn" : undefined} />
        <StatTile to="/patterns" label={t("dashboard.strong_patterns")} value={patterns.loading ? null : strongPatterns.length} hint="Confidence 75% or more" tone={strongPatterns.length ? "alert" : undefined} />
        <StatTile to="/cases" label={t("dashboard.sensitive_cases")} value={cases.loading ? null : sensitiveCases.length} hint="Access is justified & logged" />
      </div>

      {/* ── Search ── */}
      <section className="card" style={{ marginBottom: 18 }} aria-labelledby="search-h">
        <h2 id="search-h" className="card-heading">Find a person, phone, vehicle or FIR</h2>
        <form onSubmit={submitSearch} className="quick-search-bar" role="search">
          <input
            aria-label={t("common.search")}
            placeholder={t("search.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button disabled={!query.trim()}>{t("common.search")}</button>
        </form>
        <div className="suggest-row">
          {recent.length > 0 && (
            <div className="suggest-group">
              <span className="suggest-label">Recent</span>
              {recent.map((r) => (
                <button key={r} type="button" className="chip" onClick={() => go(r)}>{r}</button>
              ))}
              <button type="button" className="link-btn" onClick={() => { clearRecentSearches(); setRecent([]); }}>Clear</button>
            </div>
          )}
          {topInfluencers.length > 0 && (
            <div className="suggest-group">
              <span className="suggest-label">Key people & phones</span>
              {topInfluencers.map((r) => (
                <button key={r.entity_value} type="button" className="chip" onClick={() => go(r.entity_value)}>{r.entity_value}</button>
              ))}
            </div>
          )}
        </div>
      </section>

      {nothingYet && (
        <section className="card onboarding" style={{ marginBottom: 18 }}>
          <h2 className="card-heading">Get started</h2>
          <ol>
            <li><Link to="/ingestion">Ingest FIRs, call records or transactions</Link> so there is something to explore.</li>
            <li><Link to="/cases?new=1">Open a case</Link> to keep your findings together.</li>
            <li><Link to="/network">Explore the network</Link> and pin the people and phones that matter.</li>
          </ol>
        </section>
      )}

      <div className="dashboard-grid">
        <section className="dash-col">
          {/* Cases */}
          <Panel title="Active cases" action={<Link to="/cases" className="btn-link">View all →</Link>} state={cases}>
            <div className="case-list">
              {openCases.slice(0, 6).map((c) => (
                <Link key={c.case_id} to={`/cases/${c.case_id}`} className="case-row">
                  <div className="case-row-info">
                    <span className="case-row-title">{c.title}</span>
                    <span className="case-row-meta">{c.fir_number} · {c.jurisdiction}</span>
                  </div>
                  <div className="case-row-tags">
                    {c.is_sensitive && <span className="sensitive-tag">sensitive</span>}
                    <span className={`status-pill status-${c.status}`}>{c.status.replace("_", " ")}</span>
                  </div>
                </Link>
              ))}
              {!cases.loading && !openCases.length && !cases.error && <p className="hint">No open cases. <Link to="/cases?new=1">Create one →</Link></p>}
              {openCases.length > 6 && <Link to="/cases" className="more-link">+ {openCases.length - 6} more cases</Link>}
            </div>
          </Panel>

          {/* Patterns */}
          <Panel title="Strongest pattern detections" action={<Link to="/patterns" className="btn-link">View all →</Link>} state={patterns} skeletonRows={3}>
            <div className="pattern-mini-list">
              {strongPatterns.slice(0, 4).map((p) => (
                <Link key={p.pattern_id} to={`/patterns/${p.pattern_id}`} className="pattern-mini">
                  <div className="pattern-mini-text">
                    <span className="pattern-mini-type">{p.pattern_type.replace(/_/g, " ")} <WomenSafetyBadge pattern={p} showTier={false} /></span>
                    <span className="pattern-mini-desc">{p.description}</span>
                  </div>
                  <ConfidencePill confidence={p.confidence} />
                </Link>
              ))}
              {!patterns.loading && !patterns.error && !strongPatterns.length && <p className="hint">No new high-confidence patterns.</p>}
            </div>
            <p className="lead-note">AI-derived leads — verify before use.</p>
          </Panel>
        </section>

        <aside className="dash-col">
          {/* Alerts */}
          <Panel
            title={t("dashboard.recent_alerts")}
            action={unread.length > 0
              ? <button type="button" className="link-btn" onClick={() => void readAll()}>{t("dashboard.mark_all_read")}</button>
              : <Link to="/alerts" className="btn-link">All →</Link>}
            state={alerts}
            skeletonRows={3}
          >
            <div className="alert-list">
              {alertGroups.slice(0, 5).map(({ latest, count, unread: u }) => (
                <Link key={latest.alert_id} to={`/search?q=${encodeURIComponent(latest.entity_value)}`} className={`alert-row ${u ? "alert-unread" : ""}`}>
                  <span className={`dot ${u ? "warn" : ""}`} style={{ marginTop: 6, flexShrink: 0 }} aria-hidden="true" />
                  <div className="alert-row-body">
                    <p className="alert-row-msg">{latest.message}</p>
                    <span className="hint">{relativeTime(latest.triggered_at)}{count > 1 ? ` · fired ${count}×` : ""}{u ? "" : " · read"}</span>
                  </div>
                </Link>
              ))}
              {!alerts.loading && !alerts.error && !alertGroups.length && <p className="hint">No alerts yet. <Link to="/alerts">Watch an entity →</Link></p>}
            </div>
            {alertGroups.length > 5 && <Link to="/alerts" className="more-link">+ {alertGroups.length - 5} more</Link>}
          </Panel>

          {/* Highest-WSRS suspects (supervisor and admin only) */}
          {showSystem && (
            <Panel title={t("nav.wsrs_leaderboard")} state={highRisk} skeletonRows={3}>
              {highRisk.data && (
                <div className="pattern-mini-list">
                  {highRisk.data.suspects.map((s) => (
                    <Link key={s.suspect} to={`/entity/person/${encodeURIComponent(s.suspect)}`} className="pattern-mini">
                      <div className="pattern-mini-text">
                        <span className="pattern-mini-type">{s.suspect}</span>
                        <span className="pattern-mini-desc">{s.wsrs.factors.repeat.label} · {s.wsrs.factors.recency.label}</span>
                      </div>
                      <WsrsBadge total={s.score} tier={s.tier} />
                    </Link>
                  ))}
                  {!highRisk.data.suspects.length && <p className="hint">No suspects have a women-safety risk score yet.</p>}
                  <p className="lead-note">Investigative lead — verify before use.</p>
                </div>
              )}
            </Panel>
          )}

          {/* Repeat victims: aggregate counts only, no identifiers are ever shown */}
          {showSystem && (
            <Panel title="Repeat victims" state={victims} skeletonRows={3}>
              {victims.data && (
                <>
                  <div className="stat-list-row">
                    <dt>Victims in 2+ FIRs</dt>
                    <dd>{formatCount(victims.data.repeat_victim_count)}</dd>
                  </div>
                  <div className="suggest-group" style={{ marginTop: 10 }}>
                    {victims.data.offense_categories.map((c) => (
                      <span key={c.category} className="chip">{c.category.replace(/_/g, " ").toLowerCase()} · {c.victims}</span>
                    ))}
                    {!victims.data.offense_categories.length && <p className="hint">No repeat victimisation recorded.</p>}
                  </div>
                  <p className="lead-note">Investigative lead — verify before use. Pseudonymous; no personal data shown.</p>
                </>
              )}
            </Panel>
          )}

          {/* Quick actions */}
          <section className="card">
            <h2 className="card-heading">{t("dashboard.quick_actions")}</h2>
            <div className="quick-action-grid">
              <QuickAction to="/network" label={t("nav.network")} iconPath="M8 4c1 0 2 1 2 2s-1 2-2 2-2-1-2-2 1-2 2-2zM3 12c0 0 1-3 5-3s5 3 5 3" />
              <QuickAction to="/patterns" label={t("nav.patterns")} iconPath="M2 12l3-4 3 2 3-5 3 2" />
              <QuickAction to="/hunt" label={t("nav.hunt")} iconPath="M8 6a3 3 0 100-6 3 3 0 000 6zM2 14c0-3 2.7-6 6-6s6 3 6 6" />
              {canIngest && <QuickAction to="/ingestion" label={t("nav.ingestion")} iconPath="M8 1v9M5 7l3 3 3-3M3 13h10" />}
            </div>
          </section>

          {/* Real dataset numbers */}
          <Panel title="What's in the system" state={summary} skeletonRows={3}>
            {summary.data && (
              <dl className="stat-list">
                <Row label="FIR records" value={summary.data.firs} />
                <Row label="People (suspects)" value={summary.data.suspects} />
                <Row label="Phone numbers" value={summary.data.phones} />
                <Row label="Calls (CDR)" value={summary.data.calls} />
                <Row label="Financial accounts" value={summary.data.accounts} />
                <Row label="Transactions" value={summary.data.transactions} />
              </dl>
            )}
          </Panel>
        </aside>
      </div>
    </main>
  );
}

function Panel({
  title, action, state, skeletonRows = 4, children,
}: {
  title: string;
  action?: ReactNode;
  state: { loading: boolean; error: string; reload: () => void; data: unknown };
  skeletonRows?: number;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <div className="card-title">
        <h2 className="card-heading">{title}</h2>
        {action}
      </div>
      {state.loading && !state.data && <LoadingSkeleton rows={skeletonRows} />}
      {state.error && (
        <div className="panel-error" role="alert">
          <span>Couldn't load this section: {state.error}</span>
          <button type="button" className="link-btn" onClick={state.reload}>Retry</button>
        </div>
      )}
      {!state.error && children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-list-row">
      <dt>{label}</dt>
      <dd>{formatCount(value)}</dd>
    </div>
  );
}

function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "4px 0" }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton-row" style={{ opacity: 0.7 - i * 0.12 }} />
      ))}
    </div>
  );
}

function StatTile({ to, label, value, hint, tone }: { to: string; label: string; value: number | null; hint: string; tone?: "warn" | "alert" }) {
  return (
    <Link to={to} className={`stat-tile stat-link ${tone ? `stat-${tone}` : ""}`} role="listitem">
      <span className="stat-value">{value === null ? "–" : formatCount(value)}</span>
      <span className="stat-label">{label}</span>
      <span className="stat-hint">{hint} →</span>
    </Link>
  );
}

function QuickAction({ to, label, iconPath }: { to: string; label: string; iconPath: string }) {
  return (
    <Link to={to} className="quick-action-btn">
      <div className="quick-action-icon">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={iconPath} />
        </svg>
      </div>
      <span>{label}</span>
    </Link>
  );
}
