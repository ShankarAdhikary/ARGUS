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

const DEBOUNCE_MS = 300;
const MIN_CHARS = 2;
const MATCH_TYPE: Record<string, string> = {
  transliteration_match: "same name in another script or spelling",
  fuzzy_name: "similar spelling",
  exact_phone: "exact phone number",
};

type Outcome = { state: "pending" } | { state: "done"; decision: "confirm_merge" | "reject"; linked: boolean } | { state: "error"; message: string };

function Candidates({ query, onDecided }: { query: string; onDecided: () => void }) {
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
          <button type="button" className="link-btn" onClick={() => search(query.trim())}>Retry</button>
        </div>
      )}
      {!error && candidates && candidates.length === 0 && !loading && (
        <p className="hint">No similar names found in the system. This may be a new entity.</p>
      )}
      {!error && candidates?.map((c) => {
        const out = outcomes[c.candidate];
        const settled = out?.state === "done" || out?.state === "pending";
        return (
          <article key={c.candidate} className="resolve-card" aria-label={`Candidate ${c.candidate}`}>
            <strong>
              <Link to={`/entity/person/${encodeURIComponent(c.candidate)}`}>{c.candidate}</Link>{" "}
              <span className="hint" style={{ fontWeight: 400 }}>in the system{c.canonical ? ` (canonical: ${c.canonical})` : ""}</span>
            </strong>
            <span>
              Similarity: <strong>{Math.round(c.similarity * 100)}%</strong> · Match type: {MATCH_TYPE[c.match_type ?? ""] ?? c.match_type ?? "unknown"}
            </span>
            <span>Suggestion: {c.suggested === "merge" ? "Likely same person" : "Possible match — review"}</span>
            <div className="resolve-actions">
              <button type="button" disabled={settled} onClick={() => void decide(c, "confirm_merge")}>Confirm merge</button>
              <button type="button" className="secondary" disabled={settled} onClick={() => void decide(c, "reject")}>Reject</button>
              {out?.state === "pending" && <span className="hint" role="status">Recording…</span>}
              {out?.state === "done" && out.decision === "confirm_merge" && (
                <span className="success-msg" role="status">
                  {out.linked ? "Linked as alias ✓ (reversible; nothing was deleted)" : "Decision recorded ✓ — no graph link made (one of the names is not a person node in the graph)"}
                </span>
              )}
              {out?.state === "done" && out.decision === "reject" && <span className="success-msg" role="status">Rejected ✓</span>}
              {out?.state === "error" && <span className="error" role="alert">{out.message}</span>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function History({ reloadKey }: { reloadKey: number }) {
  const history = useAsync(() => resolutionDecisions(10));
  const { reload } = history;
  useEffect(() => { if (reloadKey > 0) reload(); }, [reloadKey, reload]);
  return (
    <section className="card" style={{ marginTop: 18 }} aria-labelledby="resolve-history-h">
      <h2 id="resolve-history-h" className="card-heading">Recent decisions</h2>
      {history.loading && !history.data && <Skeleton rows={3} />}
      {history.error && (
        <div className="panel-error" role="alert">
          <span>Couldn't load the decision history: {history.error}</span>
          <button type="button" className="link-btn" onClick={reload}>Retry</button>
        </div>
      )}
      {history.data && history.data.length === 0 && <p className="hint">No decisions recorded yet.</p>}
      {history.data && history.data.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="audit-table">
            <thead><tr><th>When</th><th>Name</th><th>Compared with</th><th>Similarity</th><th>Decision</th><th>By</th></tr></thead>
            <tbody>
              {history.data.map((d) => (
                <tr key={d.decision_id}>
                  <td>{relativeTime(d.decided_at)}</td>
                  <td>{d.name}</td>
                  <td>{d.candidate}</td>
                  <td>{d.similarity === null ? "—" : `${Math.round(d.similarity * 100)}%`}</td>
                  <td>{d.decision === "confirm_merge" ? "Linked as alias" : "Rejected"}</td>
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
  usePageTitle("Name disambiguation");
  const [query, setQuery] = useState("");
  const [decisions, setDecisions] = useState(0);
  return (
    <main>
      <PageHeader eyebrow="Entity Resolution" title="Name Disambiguation">
        Match transliterated, alias, or script-variant names to existing suspects. Decisions are logged; confirming links the two names
        as aliases in the graph (a reversible link: nothing is merged or deleted).
      </PageHeader>
      <LeadNotice />
      <section className="card" aria-labelledby="resolve-search-h">
        <h2 id="resolve-search-h" className="card-heading">Look up a name</h2>
        <div className="quick-search-bar" role="search">
          <input aria-label="Name to look up" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. Ramesh Kumar, रमेश कुमार, Rameś" autoFocus />
        </div>
        <ErrorBoundary label="the results"><Candidates query={query} onDecided={() => setDecisions((n) => n + 1)} /></ErrorBoundary>
      </section>
      <ErrorBoundary label="the decision history"><History reloadKey={decisions} /></ErrorBoundary>
    </main>
  );
}
