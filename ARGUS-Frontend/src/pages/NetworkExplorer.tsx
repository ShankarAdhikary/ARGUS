import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import LeadNotice from "../components/LeadNotice";
import NetworkGraph from "../components/NetworkGraph";
import { addCaseEntity, API_BASE_URL, centrality, entityTypeGuess, getToken, listCases, logAudit, networkAccused, networkFinancial, networkPath, networkPhone } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { CaseSummary, CentralityRow, EntityType, GraphElements } from "../types";
import { useT } from "../i18n";

interface SurveillanceSighting {
  suspect: string;
  camera: string;
  zone: string;
  timestamp: string;
  confidence: number;
}

export default function NetworkExplorer() {
  const t = useT();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const [query, setQuery] = useState(params.get("focus") ?? "");
  const [elements, setElements] = useState<GraphElements>({ nodes: [], edges: [] });
  const [selected, setSelected] = useState<string | null>(null);
  const [showIndirect, setShowIndirect] = useState(true);
  const [ranking, setRanking] = useState<CentralityRow[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedCase, setSelectedCase] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [timeline, setTimeline] = useState("2026-09-30");
  const [pathStart, setPathStart] = useState<string | null>(null);
  const [sightings, setSightings] = useState<SurveillanceSighting[]>([]);
  const [history, setHistory] = useState<string[]>([]);

  useEffect(() => {
    listCases().then(setCases).catch(() => setCases([]));
    // Surveillance sightings — non-fatal
    const token = getToken();
    fetch(`${API_BASE_URL}/api/v1/surveillance/sightings`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => r.ok ? r.json() : [])
      .then((data: SurveillanceSighting[]) => setSightings(data.slice(0, 10)))
      .catch(() => {});
    const focus = params.get("focus");
    centrality()
      .then((rows) => {
        setRanking(rows);
        // Without a focus the canvas would open empty, which reads as broken.
        // Default to the highest-ranked entity so there is always a graph.
        if (!focus && rows.length) {
          // Prefer a person: the narrative is person-centric, and a person node
          // always has FIR and handset edges, so the canvas is never a lone dot.
          const top = rows.find((r) => r.entity_type === "person") ?? rows[0];
          setQuery(top.entity_value);
          void expand(top.entity_value, top.entity_type as EntityType);
        }
      })
      .catch(() => setRanking([]));
    if (focus) void expand(focus, (params.get("type") as EntityType) ?? entityTypeGuess(focus));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function expand(value: string, type: EntityType, before = timeline, replace = false) {
    setLoading(true);
    setStatus("");
    setHistory((h) => (h.includes(value) ? h : [...h.slice(-7), value]));
    try {
      const centerId = `${type}:${value}`;
      const network =
        type === "phone"
          ? await networkPhone(value)
          : type === "financial_account"
          ? await networkFinancial(value)
          : await networkAccused(value, undefined, undefined, before);
      const newNodes: GraphElements["nodes"] = [{ data: { id: centerId, label: value, type } }];
      const newEdges: GraphElements["edges"] = [];

      if (type === "financial_account" && Array.isArray((network as Record<string, unknown>).transactions)) {
        ((network as Record<string, unknown>).transactions as Array<Record<string, unknown>>).forEach((t, i) => {
          const partnerId = t.partner_id;
          if (!partnerId) return;
          const otherId = `financial_account:${partnerId}`;
          newNodes.push({ data: { id: otherId, label: String(partnerId), type: "financial_account" } });
          newEdges.push({ data: { id: `${centerId}-t${i}`, source: centerId, target: otherId, label: `₹${t.amount ?? ""}`, direct: true } });
        });
      } else if (type === "phone" && Array.isArray((network as Record<string, unknown>).connections)) {
        ((network as Record<string, unknown>).connections as Array<Record<string, unknown>>).forEach((c, i) => {
          const connectedPhone = c.connected_phone ?? c.target;
          if (!connectedPhone) return;
          const otherId = `phone:${connectedPhone}`;
          newNodes.push({ data: { id: otherId, label: String(connectedPhone), type: "phone" } });
          newEdges.push({ data: { id: `${centerId}-e${i}`, source: centerId, target: otherId, label: "called", direct: true } });
        });
      } else if (Array.isArray((network as Record<string, unknown>).linked_firs)) {
        ((network as Record<string, unknown>).linked_firs as string[]).forEach((firId, i) => {
          const otherId = `fir:${firId}`;
          newNodes.push({ data: { id: otherId, label: String(firId), type: "fir" } });
          newEdges.push({ data: { id: `${centerId}-e${i}`, source: centerId, target: otherId, label: "linked to", direct: true } });
        });
        const phones = (network as Record<string, unknown>).phones;
        if (Array.isArray(phones)) {
          phones.forEach((phone, i) => {
            const otherId = `phone:${String(phone)}`;
            newNodes.push({ data: { id: otherId, label: String(phone), type: "phone" } });
            newEdges.push({ data: { id: `${centerId}-phone-${i}`, source: centerId, target: otherId, label: "uses phone", direct: true } });
          });
        }
        const contacts = (network as Record<string, unknown>).phone_contacts;
        if (Array.isArray(contacts)) {
          contacts.forEach((contact, i) => {
            const record = contact as Record<string, unknown>;
            const source = String(record.source ?? "");
            const target = String(record.target ?? "");
            if (!source || !target) return;
            newNodes.push({ data: { id: `phone:${source}`, label: source, type: "phone" } });
            newNodes.push({ data: { id: `phone:${target}`, label: target, type: "phone" } });
            newEdges.push({ data: { id: `${source}-${target}-${i}`, source: `phone:${source}`, target: `phone:${target}`, label: "called", direct: true } });
          });
        }
      }

      // Changing the snapshot date replaces the graph; ordinary expansion adds to it.
      setElements((prev) => mergeGraphs(replace ? { nodes: [], edges: [] } : prev, { nodes: newNodes, edges: newEdges }));
      setSelected(centerId);
      if (user) logAudit("expand_network", `${type}:${value}`, user);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Could not expand this entity.");
    } finally {
      setLoading(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    void expand(query.trim(), entityTypeGuess(query.trim()), timeline);
  }

  async function findPath() {
    if (!pathStart || !selected || pathStart === selected) return;
    setLoading(true);
    try {
      const source = pathStart.split(":").slice(1).join(":");
      const target = selected.split(":").slice(1).join(":");
      const result = await networkPath(source, target);
      setElements({ nodes: result.nodes, edges: result.edges });
      setStatus(`Shortest path found: ${result.nodes.length} nodes.`);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "No path found.");
    } finally {
      setLoading(false);
    }
  }

  function clearGraph() {
    setElements({ nodes: [], edges: [] });
    setSelected(null);
    setPathStart(null);
    setStatus("");
  }

  function exportGraph() {
    const blob = new Blob([JSON.stringify(elements, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "argus-network-view.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function pinSelected() {
    if (!selected || !selectedCase || !user) return;
    const [type, ...rest] = selected.split(":");
    const value = rest.join(":");
    await addCaseEntity(selectedCase, { entity_type: type as EntityType, entity_value: value }, user.full_name);
    setStatus("Selected entity pinned to case.");
  }

  const displayElements: GraphElements = showIndirect
    ? elements
    : { nodes: elements.nodes, edges: elements.edges.filter((e) => e.data.direct) };

  const selectedRank = ranking.find((r) => selected?.endsWith(r.entity_value));

  return (
    <main>
      <PageHeader eyebrow={t("network.eyebrow")} title={t("network.title")}>
        Center on any entity, then expand to trace direct and inferred relationships.
        {elements.nodes.length > 0 && (
          <span style={{ marginLeft: 12, background: "var(--purple-bg)", color: "var(--purple-2)", border: "1px solid var(--border-purple)", borderRadius: 4, padding: "2px 8px", fontSize: "0.8125rem", fontWeight: 700 }}>
            {elements.nodes.length} nodes · {elements.edges.length} edges
          </span>
        )}
      </PageHeader>
      <LeadNotice />

      <div className="explorer-layout">
        {/* LEFT RAIL */}
        <aside className="card explorer-rail">
          <p className="section-label">Center on entity</p>
          <form className="inline-form" onSubmit={submit}>
            <input aria-label="Entity to centre the graph on" placeholder="Name or phone…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <button disabled={loading}>{loading ? "…" : "Go"}</button>
          </form>

          {history.length > 0 && (
            <>
              <p className="section-label" style={{ marginTop: 14 }}>Recent</p>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {[...history].reverse().map((h) => (
                  <button key={h} type="button" className="ranked-row" style={{ gridTemplateColumns: "1fr" }}
                    onClick={() => { setQuery(h); void expand(h, entityTypeGuess(h)); }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h}</span>
                  </button>
                ))}
              </div>
            </>
          )}

          <p className="section-label" style={{ marginTop: 16 }}>View options</p>
          <label className="checkbox-row">
            <input type="checkbox" checked={showIndirect} onChange={(e) => setShowIndirect(e.target.checked)} />
            Show indirect edges
          </label>

          <p className="section-label" style={{ marginTop: 16 }}>Timeline snapshot</p>
          <input type="date" aria-label="Show the network as it was on this date" value={timeline} onChange={(e) => {
            setTimeline(e.target.value);
            if (query.trim()) void expand(query.trim(), entityTypeGuess(query.trim()), e.target.value, true);
          }} />

          <p className="section-label" style={{ marginTop: 16 }}>Path finding</p>
          <p className="hint" style={{ marginBottom: 6 }}>
            {pathStart
              ? `From: ${pathStart.split(":").slice(1).join(":")} — click target node then "Find Path"`
              : "Click a node to set path start"}
          </p>
          <button className="secondary" onClick={findPath} disabled={!pathStart || !selected || loading || pathStart === selected}>
            Find shortest path
          </button>

          <p className="section-label" style={{ marginTop: 16 }}>Save to case</p>
          <select aria-label="Case to save the selected node to" value={selectedCase} onChange={(e) => setSelectedCase(e.target.value)}>
            <option value="">Select a case…</option>
            {cases.map((c) => <option key={c.case_id} value={c.case_id}>{c.title}</option>)}
          </select>
          <button className="secondary" style={{ marginTop: 4 }} onClick={pinSelected} disabled={!selected || !selectedCase}>
            Pin selected node
          </button>

          <div style={{ display: "flex", gap: 6, marginTop: 14 }}>
            <button className="secondary" style={{ flex: 1, fontSize: "0.8125rem" }} onClick={exportGraph} disabled={!elements.nodes.length}>
              Export JSON
            </button>
            <button className="btn-threat" style={{ flex: 1, fontSize: "0.8125rem" }} onClick={clearGraph} disabled={!elements.nodes.length}>
              Clear
            </button>
          </div>
          {status && <p className="hint" style={{ marginTop: 8 }}>{status}</p>}
        </aside>

        {/* CANVAS */}
        <section className="card explorer-canvas" style={{ padding: 0, overflow: "hidden" }}>
          {displayElements.nodes.length ? (
            <NetworkGraph
              elements={displayElements}
              height={640}
              selectedId={selected}
              onNodeClick={(id) => {
                setSelected(id);
                if (!pathStart) setPathStart(id);
                else if (pathStart === id) setPathStart(null);
              }}
              onNodeDoubleClick={(id) => {
                const [type, ...rest] = id.split(":");
                void expand(rest.join(":"), type as EntityType);
              }}
            />
          ) : (
            <div className="empty-canvas" style={{ height: 560 }}>
              <div style={{ textAlign: "center" }}>
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="rgba(124,92,252,.3)" strokeWidth="1.2" style={{ marginBottom: 12 }}>
                  <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/>
                  <line x1="4.93" y1="4.93" x2="9.17" y2="9.17"/><line x1="14.83" y1="14.83" x2="19.07" y2="19.07"/>
                  <line x1="14.83" y1="9.17" x2="19.07" y2="4.93"/><line x1="4.93" y1="19.07" x2="9.17" y2="14.83"/>
                </svg>
                <p style={{ color: "var(--text-3)", fontSize: "0.875rem" }}>
                  Search an entity or click an influencer to start.<br />
                  <span style={{ fontSize: "0.8125rem" }}>Double-click any node to expand its network.</span>
                </p>
              </div>
            </div>
          )}
        </section>

        {/* RIGHT RAIL */}
        <aside className="card explorer-rail">
          <p className="section-label">Selected node</p>
          {selected ? (
            <>
              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{
                  width: 10, height: 10, borderRadius: "50%", flexShrink: 0,
                  background: TYPE_COLORS[selected.split(":")[0]] ?? "#64748b",
                  display: "inline-block"
                }} />
                <h3 style={{ margin: 0 }}>{selected.split(":").slice(1).join(":")}</h3>
              </div>
              <p className="hint" style={{ marginTop: 2 }}>{selected.split(":")[0].replace("_", " ")}</p>
              {selectedRank && (
                <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <div style={{ background: "var(--surface-2)", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-3)", textTransform: "uppercase", letterSpacing: ".06em" }}>PageRank</div>
                    <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--purple-2)", marginTop: 2 }}>{selectedRank.pagerank.toFixed(3)}</div>
                  </div>
                  <div style={{ background: "var(--surface-2)", padding: "8px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-3)", textTransform: "uppercase", letterSpacing: ".06em" }}>Betweenness</div>
                    <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--purple-2)", marginTop: 2 }}>{selectedRank.betweenness.toFixed(3)}</div>
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="hint">Click a node to inspect it.</p>
          )}

          <p className="section-label" style={{ marginTop: 20 }}>Top influencers</p>
          <div className="ranked-list">
            {ranking.map((r, i) => (
              <button key={r.entity_value} type="button" className="ranked-row"
                onClick={() => { setQuery(r.entity_value); void expand(r.entity_value, r.entity_type); }}>
                <span className="ranked-num">#{i + 1}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.entity_value}</span>
                <span className="hint">{r.pagerank.toFixed(2)}</span>
              </button>
            ))}
          </div>

          {sightings.length > 0 && (
            <>
              <p className="section-label" style={{ marginTop: 20 }}>Camera sightings</p>
              <div className="ranked-list">
                {sightings.map((s, i) => (
                  <button
                    key={`${s.suspect}-${i}`}
                    type="button"
                    className="ranked-row"
                    style={{ gridTemplateColumns: "1fr auto" }}
                    onClick={() => void expand(s.suspect, "person")}
                  >
                    <span style={{ gridColumn: "1/3", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.suspect}</span>
                    <span className="hint" style={{ gridColumn: "1/2", fontSize: "0.75rem" }}>{s.zone}</span>
                    <span style={{ fontSize: "0.75rem", color: s.confidence > 0.8 ? "var(--green)" : "var(--amber)" }}>{Math.round(s.confidence * 100)}%</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </aside>
      </div>
    </main>
  );
}

const TYPE_COLORS: Record<string, string> = {
  person: "#7c5cfc", phone: "#4fc3f7", fir: "#455a64",
  financial_account: "#e040fb", organization: "#00e676",
  location: "#ffd740", vehicle: "#607d8b", event: "#ff5252",
};

function mergeGraphs(a: GraphElements, b: GraphElements): GraphElements {
  const nodeIds = new Set(a.nodes.map((n) => n.data.id));
  const nodes = [...a.nodes];
  b.nodes.forEach((n) => {
    if (!nodeIds.has(n.data.id)) {
      nodes.push(n);
      nodeIds.add(n.data.id);
    }
  });
  const edgeIds = new Set(a.edges.map((e) => e.data.id));
  const edges = [...a.edges];
  b.edges.forEach((e) => {
    if (!edgeIds.has(e.data.id)) {
      edges.push(e);
      edgeIds.add(e.data.id);
    }
  });
  return { nodes, edges };
}
