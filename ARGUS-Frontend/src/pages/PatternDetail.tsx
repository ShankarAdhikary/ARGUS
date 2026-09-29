import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import LeadNotice from "../components/LeadNotice";
import ConfidencePill from "../components/ConfidencePill";
import NetworkGraph from "../components/NetworkGraph";
import SourceChip from "../components/SourceChip";
import WomenSafetyBadge from "../components/WomenSafetyBadge";
import { listPatterns, networkAccused, networkPhone, patternFeedback } from "../lib/api";
import type { EntityType, GraphElements, PatternRecord } from "../types";

export default function PatternDetail() {
  const { id = "" } = useParams();
  const [pattern, setPattern] = useState<PatternRecord | null>(null);
  const [graph, setGraph] = useState<GraphElements>({ nodes: [], edges: [] });
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      const base = await listPatterns();
      const found = base.find((p) => p.pattern_id === id) ?? null;
      setPattern(found);
      if (found) {
        if (found.source === "burner_heuristic") {
          const phone = found.entities[0];
          const network = await networkPhone(phone);
          const nodes: GraphElements["nodes"] = [{ data: { id: `phone:${phone}`, label: phone, type: "phone" } }];
          const edges: GraphElements["edges"] = [];
          if (network && Array.isArray((network as Record<string, unknown>).connections)) {
            ((network as Record<string, unknown>).connections as Array<Record<string, unknown>>).forEach((c, i) => {
              const otherId = `phone:${c.connected_phone}`;
              nodes.push({ data: { id: otherId, label: String(c.connected_phone), type: "phone" } });
              edges.push({ data: { id: `e${i}`, source: `phone:${phone}`, target: otherId, label: "called", direct: true } });
            });
          }
          setGraph({ nodes, edges });
        } else if (found.source === "graph_recurrence" || found.source === "co_accused") {
          // Show the people and the FIRs that tie them together, from the real accused network.
          const people = Array.from(new Set(found.entities)).slice(0, 2);
          const firsByPerson = await Promise.all(
            people.map(async (name) => {
              const net = (await networkAccused(name).catch(() => null)) as Record<string, unknown> | null;
              return Array.isArray(net?.linked_firs) ? (net!.linked_firs as string[]) : [];
            })
          );
          const shown = found.source === "co_accused" && firsByPerson.length === 2
            ? firsByPerson[0].filter((fir) => firsByPerson[1].includes(fir))      // FIRs both were named in
            : (firsByPerson[0] ?? []).slice(0, 30);
          const nodes: GraphElements["nodes"] = people.map((p) => ({ data: { id: `person:${p}`, label: p, type: "person" as EntityType } }));
          const edges: GraphElements["edges"] = [];
          shown.forEach((fir, i) => {
            nodes.push({ data: { id: `fir:${fir}`, label: fir, type: "fir" as EntityType } });
            people.forEach((p, j) => {
              if (found.source === "co_accused" || j === 0) edges.push({ data: { id: `f${i}-${j}`, source: `person:${p}`, target: `fir:${fir}`, label: "named in", direct: true } });
            });
          });
          setGraph({ nodes, edges });
        } else {
          const nodes: GraphElements["nodes"] = found.entities.map((e, i) => ({ data: { id: `n${i}`, label: e, type: guessType(e) } }));
          const edges: GraphElements["edges"] = nodes.slice(1).map((n, i) => ({
            data: { id: `pe${i}`, source: nodes[0].data.id, target: n.data.id, label: "associated with", direct: false },
          }));
          setGraph({ nodes, edges });
        }
      }
    }
    void load().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load the live pattern."));
  }, [id]);

  async function act(verdict: "useful" | "false_positive" | "escalated") {
    if (!pattern) return;
    try {
      setPattern(await patternFeedback(pattern.pattern_id, verdict));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save your verdict.");
    }
  }

  if (error) return <main><p className="error" role="alert">{error}</p></main>;
  if (!pattern) return <main><p className="hint">Loading pattern…</p></main>;

  return (
    <main>
      <PageHeader eyebrow={pattern.pattern_type.replace(/_/g, " ")} title={pattern.description}>
        <ConfidencePill confidence={pattern.confidence} /> <WomenSafetyBadge pattern={pattern} />
      </PageHeader>
      <LeadNotice />

      <div className="grid-2">
        <section className="card">
          <p className="section-label">Why this was flagged</p>
          <p>{pattern.explanation}</p>
          <p className="section-label" style={{ marginTop: 18 }}>Entities involved</p>
          <div className="filter-chips">
            {Array.from(new Set(pattern.entities)).map((e) => <SourceChip key={e} label={e} />)}
          </div>
          <div className="page-actions" style={{ marginTop: 18 }}>
            <button onClick={() => act("useful")}>Confirm as useful lead</button>
            <button className="secondary" onClick={() => act("false_positive")}>Dismiss as false positive</button>
            <button className="secondary" onClick={() => act("escalated")}>Escalate to supervisor</button>
          </div>
        </section>
        <section className="card">
          <p className="section-label">Supporting sub-graph</p>
          {graph.nodes.length ? <NetworkGraph elements={graph} height={320} /> : <p className="hint">No graph data available.</p>}
        </section>
      </div>

      <Link className="view-all" to="/patterns">← Back to patterns</Link>
    </main>
  );
}

function guessType(value: string): EntityType {
  if (/^\d{6,}$/.test(value)) return "phone";
  if (/^A\/C-/.test(value)) return "financial_account";
  if (/terminal|road|street|market/i.test(value)) return "location";
  return "person";
}
