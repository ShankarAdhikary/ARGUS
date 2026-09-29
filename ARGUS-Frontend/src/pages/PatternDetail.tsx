import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import LeadNotice from "../components/LeadNotice";
import ConfidencePill from "../components/ConfidencePill";
import NetworkGraph from "../components/NetworkGraph";
import SourceChip from "../components/SourceChip";
import { burners, listPatterns, networkPhone, patternFeedback } from "../lib/api";
import type { EntityType, GraphElements, PatternRecord } from "../types";

export default function PatternDetail() {
  const { id = "" } = useParams();
  const [pattern, setPattern] = useState<PatternRecord | null>(null);
  const [graph, setGraph] = useState<GraphElements>({ nodes: [], edges: [] });
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      const [base, burnerData] = await Promise.all([listPatterns(), burners(2)]);
      let found = base.find((p) => p.pattern_id === id) ?? null;
      if (!found && burnerData?.burners?.length) {
        const burnerPatterns: PatternRecord[] = burnerData.burners.map((b, i) => ({
          pattern_id: `burner-${b.phone}-${i}`,
          pattern_type: "suspected_burner_phone",
          confidence: Math.min(0.95, 0.5 + b.calls * 0.08),
          description: `${b.phone} placed ${b.calls} outgoing calls matching a burner-phone usage pattern.`,
          explanation: `High call-out volume in a short window with no reciprocal call history is a known burner-phone signature. Threshold: ≥2 calls flagged for review.`,
          entities: [b.phone],
          detected_at: new Date().toISOString(),
          status: "new",
          source: "burner_heuristic",
        }));
        found = burnerPatterns.find((p) => p.pattern_id === id) ?? null;
      }
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
    if (pattern.source !== "burner_heuristic") {
      const updated = await patternFeedback(pattern.pattern_id, verdict);
      setPattern(updated);
    } else {
      setPattern({ ...pattern, status: verdict === "useful" ? "confirmed" : verdict === "escalated" ? "escalated" : "dismissed" });
    }
  }

  if (error) return <main><p className="error" role="alert">{error}</p></main>;
  if (!pattern) return <main><p className="hint">Loading pattern…</p></main>;

  return (
    <main>
      <PageHeader eyebrow={pattern.pattern_type.replace(/_/g, " ")} title={pattern.description}>
        <ConfidencePill confidence={pattern.confidence} />
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
