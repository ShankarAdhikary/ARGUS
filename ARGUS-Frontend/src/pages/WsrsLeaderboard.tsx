import { Fragment, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ErrorBoundary from "../components/ErrorBoundary";
import LeadNotice from "../components/LeadNotice";
import PageHeader from "../components/PageHeader";
import Skeleton from "../components/Skeleton";
import WsrsBadge from "../components/WsrsBadge";
import WsrsBreakdown from "../components/WsrsBreakdown";
import { wsrsLeaderboard } from "../lib/api";
import { useAuth } from "../lib/auth";
import { relativeTime } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";
import type { WsrsLeaderboardResponse } from "../types";

const TIERS = ["", "HIGH", "MEDIUM", "LOW"] as const;
const LIMITS = [25, 50, 100];

function Board() {
  const { user } = useAuth();
  const [tier, setTier] = useState<string>("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [limit, setLimit] = useState(50);
  const [data, setData] = useState<WsrsLeaderboardResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<Set<string>>(new Set());
  // The API has no "list jurisdictions" call, so the dropdown offers the caller's own plus every jurisdiction seen so far.
  const [known, setKnown] = useState<string[]>(user?.jurisdiction ? [user.jurisdiction] : []);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    wsrsLeaderboard({ limit, tier: tier || undefined, jurisdiction: jurisdiction || undefined })
      .then((res) => {
        setData(res);
        setOpen(new Set());
        setKnown((prev) => [...new Set([...prev, ...res.suspects.flatMap((s) => s.jurisdictions)])].sort());
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load the leaderboard."))
      .finally(() => setLoading(false));
  }, [limit, tier, jurisdiction]);

  useEffect(load, [load]);

  function toggle(name: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  const filtered = Boolean(tier || jurisdiction);
  return (
    <>
      <section className="card" style={{ marginBottom: 14 }} aria-label="Filters">
        <div className="page-actions" style={{ alignItems: "flex-end" }}>
          <label>
            Jurisdiction
            <select value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
              <option value="">All jurisdictions</option>
              {known.map((j) => <option key={j} value={j}>{j}</option>)}
            </select>
          </label>
          <label>
            Risk tier
            <select value={tier} onChange={(e) => setTier(e.target.value)}>
              {TIERS.map((t) => <option key={t} value={t}>{t || "All tiers"}</option>)}
            </select>
          </label>
          <label>
            Show
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {LIMITS.map((n) => <option key={n} value={n}>Top {n}</option>)}
            </select>
          </label>
          {filtered && <button type="button" className="secondary" onClick={() => { setTier(""); setJurisdiction(""); }}>Clear filters</button>}
        </div>
      </section>

      <section className="card" aria-busy={loading} aria-labelledby="wsrs-board-h">
        <div className="card-title">
          <h2 id="wsrs-board-h" className="card-heading">Suspects by Women Safety Risk Score</h2>
          {data?.computed_at && <span className="hint">Scores computed {relativeTime(data.computed_at)}</span>}
        </div>
        {loading && !data && <Skeleton rows={6} />}
        {error && (
          <div className="panel-error" role="alert">
            <span>Couldn't load the leaderboard: {error}</span>
            <button type="button" className="link-btn" onClick={load}>Retry</button>
          </div>
        )}
        {data && !error && data.suspects.length === 0 && (
          <p className="hint">
            {filtered
              ? "No suspects match these filters."
              : "No suspects with WSRS scores in this jurisdiction yet. Ingest FIRs to populate scores."}
          </p>
        )}
        {data && !error && data.suspects.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="audit-table">
              <thead>
                <tr><th>#</th><th>Suspect</th><th>WSRS</th><th>Tier</th><th>Jurisdiction</th><th title="All FIRs linked to this person; the breakdown shows how many are women-safety FIRs">FIRs</th><th /></tr>
              </thead>
              <tbody>
                {data.suspects.map((row, i) => {
                  const expanded = open.has(row.suspect);
                  const panelId = `wsrs-detail-${i}`;
                  return (
                    <Fragment key={row.suspect}>
                      <tr>
                        <td>{i + 1}</td>
                        <td><Link to={`/entity/person/${encodeURIComponent(row.suspect)}`}>{row.suspect}</Link></td>
                        <td style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>{row.score.toFixed(1)}</td>
                        <td><WsrsBadge total={row.score} tier={row.tier} /></td>
                        <td>{row.jurisdictions.length ? row.jurisdictions.join(", ") : <span className="hint">not recorded</span>}</td>
                        <td>{row.fir_count} <span className="hint">({row.wsrs.ws_fir_count} women-safety)</span></td>
                        <td>
                          <button type="button" className="link-btn" aria-expanded={expanded} aria-controls={panelId} onClick={() => toggle(row.suspect)}>
                            {expanded ? "Hide breakdown" : "View breakdown"}
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr id={panelId}>
                          <td colSpan={7}>
                            <div style={{ padding: "8px 4px 14px" }}>
                              <WsrsBreakdown breakdown={row.wsrs} />
                              <p className="lead-note">
                                Investigative lead — verify before use. Confidence {Math.round(row.wsrs.confidence * 100)}%. {row.wsrs.explanation}
                              </p>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

export default function WsrsLeaderboard() {
  usePageTitle("High-risk suspects");
  return (
    <main>
      <PageHeader eyebrow="Women Safety Intelligence" title="High-Risk Suspect Leaderboard">
        WSRS scores are investigative leads — verify before action. Scores are recomputed whenever FIRs are ingested; the recency factor
        only moves on the next ingest or an admin recompute, so check the "computed" time.
      </PageHeader>
      <LeadNotice />
      <ErrorBoundary label="the leaderboard"><Board /></ErrorBoundary>
    </main>
  );
}
