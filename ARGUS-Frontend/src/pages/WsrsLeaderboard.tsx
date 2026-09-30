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
import { TKey, useT } from "../i18n";

const TIERS = ["", "HIGH", "MEDIUM", "LOW"] as const;
const LIMITS = [25, 50, 100];

function Board() {
  const { user } = useAuth();
  const t = useT();
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
      <section className="card" style={{ marginBottom: 14 }} aria-label={t("common.filter")}>
        <div className="page-actions" style={{ alignItems: "flex-end" }}>
          <label>
            {t("wsrs.jurisdiction")}
            <select value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
              <option value="">{t("common.all")}</option>
              {known.map((j) => <option key={j} value={j}>{j}</option>)}
            </select>
          </label>
          <label>
            {t("wsrs.tier")}
            <select value={tier} onChange={(e) => setTier(e.target.value)}>
              {TIERS.map((v) => <option key={v} value={v}>{v ? t(`wsrs.${v.toLowerCase()}` as TKey) : t("common.all")}</option>)}
            </select>
          </label>
          <label>
            {t("common.view")}
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {LIMITS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          {filtered && <button type="button" className="secondary" onClick={() => { setTier(""); setJurisdiction(""); }}>{t("common.clear")}</button>}
        </div>
      </section>

      <section className="card" aria-busy={loading} aria-labelledby="wsrs-board-h">
        <div className="card-title">
          <h2 id="wsrs-board-h" className="card-heading">{t("wsrs.title")}</h2>
          {data?.computed_at && <span className="hint">Scores computed {relativeTime(data.computed_at)}</span>}
        </div>
        {loading && !data && <Skeleton rows={6} />}
        {error && (
          <div className="panel-error" role="alert">
            <span>Couldn't load the leaderboard: {error}</span>
            <button type="button" className="link-btn" onClick={load}>{t("common.retry")}</button>
          </div>
        )}
        {data && !error && data.suspects.length === 0 && (
          <p className="hint">
            {filtered
              ? "No suspects match these filters."
              : t("wsrs.no_suspects")}
          </p>
        )}
        {data && !error && data.suspects.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="audit-table">
              <thead>
                <tr><th>{t("wsrs.rank")}</th><th>{t("wsrs.name")}</th><th>{t("wsrs.score")}</th><th>{t("wsrs.tier")}</th><th>{t("wsrs.jurisdiction")}</th><th title="All FIRs linked to this person; the breakdown shows how many are women-safety FIRs">{t("wsrs.fir_count")}</th><th /></tr>
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
                            {expanded ? t("wsrs.hide_breakdown") : t("wsrs.view_breakdown")}
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
  const t = useT();
  usePageTitle(t("wsrs.title"));
  return (
    <main>
      <PageHeader eyebrow={t("wsrs.eyebrow")} title={t("wsrs.title")}>
        {t("wsrs.subtitle")}
      </PageHeader>
      <LeadNotice>{t("lead_notice.wsrs")}</LeadNotice>
      <ErrorBoundary label="the leaderboard"><Board /></ErrorBoundary>
    </main>
  );
}
