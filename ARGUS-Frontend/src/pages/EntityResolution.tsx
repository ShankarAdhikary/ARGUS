import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import ErrorBoundary from "../components/ErrorBoundary";
import LeadNotice from "../components/LeadNotice";
import PageHeader from "../components/PageHeader";
import Skeleton from "../components/Skeleton";
import { resolutionDecisions, resolveCheck, resolveDecision } from "../lib/api";
import type { ResolveCandidate } from "../lib/api";
import { relativeTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { usePageTitle } from "../lib/usePageTitle";
import { useT } from "../i18n";

const DEBOUNCE_MS = 300;
const MIN_CHARS = 2;
const MATCH_TYPE: Record<string, string> = {
  transliteration_match: "same name in another script or spelling",
  fuzzy_name: "similar spelling",
  exact_phone: "exact phone number",
};

type Outcome = { state: "pending" } | { state: "done"; decision: "confirm_merge" | "reject"; linked: boolean } | { state: "error"; message: string };

function Candidates({ query, onDecided }: { query: string; onDecided: () => void }) {
  const t = useT();
  const [candidates, setCandidates] = useState<ResolveCandidate[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const seq = useRef(0);

  const search = useCallback((name: string) => {
    const mine = ++seq.current;          // a slower, older response must never overwrite a newer one
    setLoading(true);
    setError("");
    resolveCheck(name)
      .then((rows) => { if (mine === seq.current) { setCandidates(rows); setOutcomes({}); } })
      .catch((err: unknown) => { if (mine === seq.current) setError(err instanceof Error ? err.message : "Search failed."); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, []);

  useEffect(() => {
    const name = query.trim();
    if (name.length < MIN_CHARS) {
      seq.current++;
      setCandidates(null);
      setLoading(false);
      setError("");
      return;
    }
    const timer = window.setTimeout(() => search(name), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, search]);

  async function decide(c: ResolveCandidate, decision: "confirm_merge" | "reject") {
    setOutcomes((o) => ({ ...o, [c.candidate]: { state: "pending" } }));
    try {
      const res = await resolveDecision(query.trim(), c.candidate, decision, c.similarity);
      setOutcomes((o) => ({ ...o, [c.candidate]: { state: "done", decision, linked: res.graph_linked } }));
      onDecided();
    } catch (err) {
      setOutcomes((o) => ({ ...o, [c.candidate]: { state: "error", message: err instanceof Error ? err.message : "Could not record the decision." } }));
    }
  }

  if (query.trim().length < MIN_CHARS) return <p className="hint">Type at least {MIN_CHARS} characters of a name (any script) to look for matches.</p>;
  return (
    <div aria-live="polite" aria-busy={loading}>
      {loading && !candidates && <Skeleton rows={3} />}
      {error && (
        <div className="panel-error" role="alert">
          <span>Couldn't search: {error}</span>
          <button type="button" className="link-btn" onClick={() => search(query.trim())}>{t("common.retry")}</button>
        </div>
      )}
      {!error && candidates && candidates.length === 0 && !loading && (
        <p className="hint">{t("resolve.no_candidates")}</p>
      )}
      {!error && candidates?.map((c) => {
        const out = outcomes[c.candidate];
        const settled = out?.state === "done" || out?.state === "pending";
        return (
          <article key={c.candidate} className="resolve-card" aria-label={`${t("resolve.candidates")}: ${c.candidate}`}>
            <strong>
              <Link to={`/entity/person/${encodeURIComponent(c.candidate)}`}>{c.candidate}</Link>{" "}
              <span className="hint" style={{ fontWeight: 400 }}>in the system{c.canonical ? ` (canonical: ${c.canonical})` : ""}</span>
            </strong>
            <span>
              {t("resolve.similarity")}: <strong>{Math.round(c.similarity * 100)}%</strong> · {t("resolve.match_type")}: {MATCH_TYPE[c.match_type ?? ""] ?? c.match_type ?? "unknown"}
            </span>
            <span>{c.suggested === "merge" ? t("resolve.suggested_merge") : t("resolve.suggested_review")}</span>
            <div className="resolve-actions">
              <button type="button" disabled={settled} onClick={() => void decide(c, "confirm_merge")}>{t("resolve.confirm_merge")}</button>
              <button type="button" className="secondary" disabled={settled} onClick={() => void decide(c, "reject")}>{t("resolve.reject")}</button>
              {out?.state === "pending" && <span className="hint" role="status">Recording…</span>}
              {out?.state === "done" && out.decision === "confirm_merge" && (
                <span className="success-msg" role="status">
                  {out.linked ? t("resolve.linked") : t("resolve.no_graph_link")}
                </span>
              )}
              {out?.state === "done" && out.decision === "reject" && <span className="success-msg" role="status">{t("resolve.rejected")}</span>}
              {out?.state === "error" && <span className="error" role="alert">{out.message}</span>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function History({ reloadKey }: { reloadKey: number }) {
  const t = useT();
  const history = useAsync(() => resolutionDecisions(10));
  const { reload } = history;
  useEffect(() => { if (reloadKey > 0) reload(); }, [reloadKey, reload]);
  return (
    <section className="card" style={{ marginTop: 18 }} aria-labelledby="resolve-history-h">
      <h2 id="resolve-history-h" className="card-heading">{t("resolve.history")}</h2>
      {history.loading && !history.data && <Skeleton rows={3} />}
      {history.error && (
        <div className="panel-error" role="alert">
          <span>Couldn't load the decision history: {history.error}</span>
          <button type="button" className="link-btn" onClick={reload}>{t("common.retry")}</button>
        </div>
      )}
      {history.data && history.data.length === 0 && <p className="hint">No decisions recorded yet.</p>}
      {history.data && history.data.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="audit-table">
            <thead><tr><th>{t("common.date")}</th><th>{t("wsrs.name")}</th><th>{t("resolve.candidates")}</th><th>{t("resolve.similarity")}</th><th>{t("common.status")}</th><th>{t("common.by")}</th></tr></thead>
            <tbody>
              {history.data.map((d) => (
                <tr key={d.decision_id}>
                  <td>{relativeTime(d.decided_at)}</td>
                  <td>{d.name}</td>
                  <td>{d.candidate}</td>
                  <td>{d.similarity === null ? "—" : `${Math.round(d.similarity * 100)}%`}</td>
                  <td>{d.decision === "confirm_merge" ? t("resolve.linked") : t("resolve.rejected")}</td>
                  <td>{d.decided_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function EntityResolution() {
  const t = useT();
  usePageTitle(t("resolve.title"));
  const [query, setQuery] = useState("");
  const [decisions, setDecisions] = useState(0);
  return (
    <main>
      <PageHeader eyebrow={t("resolve.eyebrow")} title={t("resolve.title")}>
        {t("resolve.subtitle")}
      </PageHeader>
      <LeadNotice />
      <section className="card" aria-labelledby="resolve-search-h">
        <h2 id="resolve-search-h" className="card-heading">{t("common.search")}</h2>
        <div className="quick-search-bar" role="search">
          <input aria-label="Name to look up" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("resolve.search_placeholder")} autoFocus />
        </div>
        <ErrorBoundary label="the results"><Candidates query={query} onDecided={() => setDecisions((n) => n + 1)} /></ErrorBoundary>
      </section>
      <ErrorBoundary label="the decision history"><History reloadKey={decisions} /></ErrorBoundary>
    </main>
  );
}
