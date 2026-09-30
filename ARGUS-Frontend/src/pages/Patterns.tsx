import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import LeadNotice from "../components/LeadNotice";
import ConfidencePill from "../components/ConfidencePill";
import SourceChip from "../components/SourceChip";
import WomenSafetyBadge from "../components/WomenSafetyBadge";
import { API_BASE_URL, getToken, listPatterns, patternFeedback } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { PatternRecord } from "../types";
import { useT } from "../i18n";

interface CommunityCluster {
  cluster_id: number;
  size: number;
  members: string[];
  explanation: string;
}

export default function Patterns() {
  const t = useT();
  const { user } = useAuth();
  const [patterns, setPatterns] = useState<PatternRecord[]>([]);
  const [communities, setCommunities] = useState<CommunityCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"all" | "new" | "confirmed" | "dismissed" | "escalated">("all");
  const [womenSafetyOnly, setWomenSafetyOnly] = useState(false);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      // The API already returns every detected pattern (phone hubs included), so nothing is synthesised here.
      const combined = await listPatterns();
      setPatterns(combined);
      try {
        const token = getToken();
        const resp = await fetch(`${API_BASE_URL}/api/v1/analytics/communities`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (resp.ok) setCommunities(await resp.json() as CommunityCluster[]);
      } catch { /* optional */ }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load patterns.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function act(pattern: PatternRecord, verdict: "useful" | "false_positive" | "escalated") {
    if (pattern.source === "burner_heuristic") {
      setPatterns((prev) => prev.map((p) => (p.pattern_id === pattern.pattern_id
        ? { ...p, status: verdict === "useful" ? "confirmed" : verdict === "escalated" ? "escalated" : "dismissed" }
        : p)));
    } else {
      const updated = await patternFeedback(pattern.pattern_id, verdict);
      setPatterns((prev) => prev.map((p) => (p.pattern_id === pattern.pattern_id ? updated : p)));
    }
    void user;
  }

  const filtered = patterns.filter((p) => (filter === "all" || p.status === filter) && (!womenSafetyOnly || p.women_safety_flag));
  const womenSafetyCount = patterns.filter((p) => p.women_safety_flag).length;

  const countByStatus = (s: string) => patterns.filter((p) => p.status === s).length;

  return (
    <main>
      <PageHeader eyebrow={t("patterns.eyebrow")} title={t("patterns.title")}>
        AI-flagged leads — phone hubs, repeat offenders, co-accused clusters, financial structuring and network hubs. Patterns rooted in trafficking-type cases carry a Women Safety badge. Confirm or dismiss each finding to improve future runs.
      </PageHeader>
      <LeadNotice />

      {/* Summary stats */}
      <div className="stat-row" style={{ marginBottom: 16 }}>
        <div className="stat-tile"><span className="stat-value">{patterns.length}</span><span className="stat-label">Total patterns</span></div>
        <div className="stat-tile stat-alert"><span className="stat-value">{countByStatus("new")}</span><span className="stat-label">Awaiting review</span></div>
        <div className="stat-tile stat-success"><span className="stat-value">{countByStatus("confirmed")}</span><span className="stat-label">Confirmed</span></div>
        <div className="stat-tile"><span className="stat-value">{countByStatus("dismissed")}</span><span className="stat-label">Dismissed</span></div>
        <div className="stat-tile stat-warn"><span className="stat-value">{countByStatus("escalated")}</span><span className="stat-label">Escalated</span></div>
      </div>

      {/* Pattern type breakdown */}
      {patterns.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <span className="section-label">Pattern type breakdown</span>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
            {Object.entries(
              patterns.reduce<Record<string, number>>((acc, p) => {
                const key = p.pattern_type.replace(/_/g, " ");
                acc[key] = (acc[key] ?? 0) + 1;
                return acc;
              }, {})
            )
              .sort((a, b) => b[1] - a[1])
              .map(([type, count]) => {
                const pct = Math.round((count / patterns.length) * 100);
                const typeColor = type.includes("burner") || type.includes("phone")
                  ? "#4fc3f7"
                  : type.includes("financial")
                  ? "#e040fb"
                  : type.includes("central") || type.includes("hub")
                  ? "#7c5cfc"
                  : "#00e676";
                return (
                  <div key={type} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ width: 160, fontSize: "0.75rem", color: "var(--text-2)", textTransform: "capitalize", flexShrink: 0 }}>
                      {type}
                    </span>
                    <div style={{ flex: 1, height: 6, background: "var(--surface-2)", borderRadius: 3, overflow: "hidden" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: typeColor, borderRadius: 3, transition: "width .4s ease" }} />
                    </div>
                    <span style={{ fontSize: "0.75rem", fontFamily: "monospace", color: "var(--text-3)", width: 40, textAlign: "right" }}>
                      {count} ({pct}%)
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      )}

      <div className="card sticky-filter" style={{ marginBottom: 14 }}>
        <div className="filter-chips" style={{ marginTop: 0 }}>
          {(["all", "new", "confirmed", "dismissed", "escalated"] as const).map((f) => (
            <button key={f} type="button" className={`chip ${filter === f ? "chip-active" : ""}`} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
          <button
            type="button"
            className={`chip ${womenSafetyOnly ? "chip-active" : ""}`}
            aria-pressed={womenSafetyOnly}
            onClick={() => setWomenSafetyOnly((v) => !v)}
          >
            ⚠ Women Safety ({womenSafetyCount})
          </button>
        </div>
      </div>

      {loading && (
        <div className="page-loading">
          <span className="spinner" /> Running pattern detection…
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}

      <div className="pattern-list">
        {filtered.map((p) => (
          <div key={p.pattern_id} className="card pattern-card">
            <div className="pattern-card-top">
              <div style={{ minWidth: 0 }}>
                <span className="section-label">{p.pattern_type.replace(/_/g, " ")}</span>
                <WomenSafetyBadge pattern={p} />
                <p style={{ margin: 0, fontWeight: 600, color: "var(--text)", fontSize: "0.88rem" }}>{p.description}</p>
                {/* Confidence bar */}
                <div className="confidence-bar" style={{ width: 180 }}>
                  <div
                    className="confidence-bar-fill"
                    style={{
                      width: `${Math.round(p.confidence * 100)}%`,
                      background: p.confidence >= 0.75 ? "var(--green)" : p.confidence >= 0.5 ? "var(--amber)" : "var(--red)",
                    }}
                  />
                </div>
              </div>
              <ConfidencePill confidence={p.confidence} />
            </div>
            <div className="pattern-card-sources">
              <span className="hint">Derived from:</span>
              <SourceChip label={p.source.replace(/_/g, " ")} />
              {Array.from(new Set(p.entities)).slice(0, 4).map((entity) => (
                <SourceChip key={entity} label={entity} />
              ))}
              {p.entities.length > 4 && <span className="hint">+{p.entities.length - 4} more</span>}
            </div>
            <div className="pattern-card-meta">
              <span className="hint">{p.entities.length} entit{p.entities.length === 1 ? "y" : "ies"}</span>
              <span className={`status-pill status-${p.status === "new" ? "open" : p.status}`}>{p.status}</span>
              <Link to={`/patterns/${p.pattern_id}`} style={{ fontSize: "0.8125rem" }}>Details →</Link>
            </div>
            <div className="page-actions">
              <button className="secondary" style={{ fontSize: "0.8125rem" }} onClick={() => act(p, "useful")}>Confirm useful</button>
              <button className="secondary" style={{ fontSize: "0.8125rem" }} onClick={() => act(p, "false_positive")}>Dismiss</button>
              <button className="secondary" style={{ fontSize: "0.8125rem" }} onClick={() => act(p, "escalated")}>Escalate</button>
            </div>
          </div>
        ))}
        {!loading && !filtered.length && <p className="hint">No patterns in this filter.</p>}
      </div>

      {communities.length > 0 && !womenSafetyOnly && (
        <section style={{ marginTop: 28 }}>
          <h2>Call-Network Communities (Louvain)</h2>
          <p className="hint" style={{ marginBottom: 12 }}>Auto-discovered calling clusters. Each group contacts each other far more than the rest of the network — consistent with a single operating cell.</p>
          <div className="pattern-list">
            {communities.slice(0, 8).map((c) => (
              <div key={c.cluster_id} className="card pattern-card">
                <div className="pattern-card-top">
                  <div>
                    <span className="section-label">Cell {c.cluster_id}</span>
                    <p style={{ margin: 0, color: "var(--text-2)", fontSize: "0.875rem" }}>{c.explanation}</p>
                  </div>
                  <span className="status-pill status-open">{c.size} numbers</span>
                </div>
                <div className="pattern-card-sources">
                  <span className="hint">Members:</span>
                  {Array.from(new Set(c.members)).slice(0, 6).map((m) => (
                    <SourceChip key={m} label={m} />
                  ))}
                  {c.members.length > 6 && <span className="hint">+{c.members.length - 6} more</span>}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
