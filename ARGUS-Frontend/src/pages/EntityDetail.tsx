import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import LeadNotice from "../components/LeadNotice";
import NetworkGraph from "../components/NetworkGraph";
import { addCaseEntity, createAlertRule, getCase, listCases, logAudit, networkAccused, networkFinancial, networkPhone, searchMasterDossier } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { CaseDetail, CaseSummary, EntityType, GraphElements } from "../types";

type Tab = "overview" | "relationships" | "cases" | "notes";

export default function EntityDetail() {
  const { type = "person", value = "" } = useParams<{ type: EntityType; value: string }>();
  const decodedValue = decodeURIComponent(value);
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [record, setRecord] = useState<Record<string, unknown> | null>(null);
  const [graph, setGraph] = useState<GraphElements>({ nodes: [], edges: [] });
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [caseDetails, setCaseDetails] = useState<CaseDetail[]>([]);
  const [selectedCase, setSelectedCase] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setStatus("");
    logAudit("view_entity", `${type}:${decodedValue}`, user!);
    Promise.all([
    searchMasterDossier(decodedValue),
    type === "phone" ? networkPhone(decodedValue) : type === "financial_account" ? networkFinancial(decodedValue) : networkAccused(decodedValue),
    listCases(),
    ]).then(([dossier, network, caseList]) => {
      if (dossier?.dossier_records?.length) setRecord(dossier.dossier_records[0]);
      setGraph(buildGraph(type as EntityType, decodedValue, network));
      setCases(caseList);
      // Sensitive cases need a justification, so never open them implicitly from here
      // (it would only trigger a 428 and a "denied" audit entry per case).
      Promise.all(caseList.filter((c) => !c.is_sensitive).map((c) => getCase(c.case_id).catch(() => null))).then((details) =>
        setCaseDetails(details.filter((d): d is CaseDetail => Boolean(d)))
      );
      setLoading(false);
    }).catch((err: unknown) => {
      setStatus(err instanceof Error ? err.message : "Could not load the live entity profile.");
      setLoading(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, decodedValue]);

  const linkedCases = useMemo(
    () => caseDetails.filter((c) => c.entities.some((e) => e.entity_value === decodedValue)),
    [caseDetails, decodedValue]
  );

  async function pinToCase() {
    if (!selectedCase || !user) return;
    await addCaseEntity(selectedCase, { entity_type: type as EntityType, entity_value: decodedValue }, user.full_name);
    logAudit("pin_entity_to_case", `${type}:${decodedValue} -> ${selectedCase}`, user);
    setStatus(`Pinned to case.`);
  }

  async function watchEntity() {
    await createAlertRule(decodedValue);
    if (user) logAudit("create_alert_rule", `${type}:${decodedValue}`, user);
    setStatus("Watch rule created — you'll be alerted when this entity appears in new records.");
  }

  return (
    <main>
      <PageHeader eyebrow={(type as string).replace("_", " ")} title={decodedValue}>
        {loading ? "Loading profile…" : record ? "Source-confirmed profile with linked network." : "No structured source record found — showing graph relationships only."}
      </PageHeader>
      <LeadNotice />

      <div className="page-actions" style={{ marginBottom: 18 }}>
        <select aria-label="Add this entity to a case" value={selectedCase} onChange={(e) => setSelectedCase(e.target.value)}>
          <option value="">Add to case…</option>
          {cases.map((c) => (
            <option key={c.case_id} value={c.case_id}>{c.title}</option>
          ))}
        </select>
        <button className="secondary" onClick={pinToCase} disabled={!selectedCase}>Add to case</button>
        <button className="secondary" onClick={watchEntity}>Add to watchlist</button>
        <Link className="secondary btn-link" to={`/network?focus=${encodeURIComponent(decodedValue)}&type=${type}`}>Explore in network view</Link>
      </div>
      {status && <p className="hint">{status}</p>}

      <div className="tab-bar">
        {(["overview", "relationships", "cases", "notes"] as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? "tab-active" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === "overview" && (
        <section className="card">
          {record ? (
            <dl className="kv-grid">
              {Object.entries(record)
                .filter(([k]) => k !== "Image")
                .map(([k, v]) => (
                  <div key={k}>
                    <dt>{k.replace(/_/g, " ")}</dt>
                    <dd>{String(v)}</dd>
                  </div>
                ))}
            </dl>
          ) : (
            <p className="hint">No indexed FIR record for this exact value yet. Try searching a variant, or check the graph tab for inferred connections.</p>
          )}
        </section>
      )}

      {tab === "relationships" && (
        <section className="card">
          {graph.nodes.length > 1 ? (
            <NetworkGraph elements={graph} height={380} />
          ) : (
            <p className="hint">No relationships found in the graph for this entity yet.</p>
          )}
        </section>
      )}

      {tab === "cases" && (
        <section className="card">
          {linkedCases.length ? (
            <div className="case-list">
              {linkedCases.map((c) => (
                <Link key={c.case_id} to={`/cases/${c.case_id}`} className="case-row">
                  <strong>{c.title}</strong>
                  <span className={`status-pill status-${c.status}`}>{c.status}</span>
                </Link>
              ))}
            </div>
          ) : (
            <p className="hint">Not pinned to any case yet.</p>
          )}
        </section>
      )}

      {tab === "notes" && (
        <section className="card">
          <p className="hint">Notes live at the case level. Open a linked case to add investigator notes about this entity.</p>
        </section>
      )}
    </main>
  );
}

function buildGraph(type: EntityType, value: string, network: Record<string, unknown> | null): GraphElements {
  const centerId = `${type}:${value}`;
  const nodes: GraphElements["nodes"] = [{ data: { id: centerId, label: value, type } }];
  const edges: GraphElements["edges"] = [];

  if (!network) return { nodes, edges };

  if (type === "financial_account" && Array.isArray(network.transactions)) {
    const seen = new Set<string>([centerId]);
    (network.transactions as Array<Record<string, unknown>>).forEach((t, i) => {
      const partnerId = String(t.partner_id ?? "");
      if (!partnerId) return;
      const otherId = `financial_account:${partnerId}`;
      if (!seen.has(otherId)) {
        seen.add(otherId);
        nodes.push({ data: { id: otherId, label: partnerId, type: "financial_account" } });
      }
      edges.push({ data: { id: `ft${i}`, source: centerId, target: otherId, label: `₹${t.amount ?? ""}`, direct: true } });
    });
    return { nodes, edges };
  }

  if (type === "phone" && (Array.isArray(network.connections) || Array.isArray(network.users))) {
    const seenPhone = new Set<string>([centerId]);
    (network.connections as Array<Record<string, unknown>> | undefined)?.forEach((conn, i) => {
      const otherId = `phone:${conn.connected_phone}`;
      if (seenPhone.has(otherId)) return;
      seenPhone.add(otherId);
      nodes.push({ data: { id: otherId, label: String(conn.connected_phone), type: "phone" } });
      edges.push({ data: { id: `e${i}`, source: centerId, target: otherId, label: "called", direct: true } });
    });
    // Suspects attributed to this handset — without these a phone with no call
    // records renders as an isolated node even when its owner is known.
    (network.users as string[] | undefined)?.forEach((person, i) => {
      const personId = `person:${person}`;
      if (seenPhone.has(personId)) return;
      seenPhone.add(personId);
      nodes.push({ data: { id: personId, label: person, type: "person" } });
      edges.push({ data: { id: `usr${i}`, source: personId, target: centerId, label: "uses", direct: true } });
    });
  } else {
    const seen = new Set<string>([centerId]);
    const addNode = (id: string, label: string, nodeType: EntityType) => {
      if (seen.has(id)) return;
      seen.add(id);
      nodes.push({ data: { id, label, type: nodeType } });
    };

    if (Array.isArray(network.linked_firs)) {
      (network.linked_firs as string[]).forEach((firId, i) => {
        const otherId = `fir:${firId}`;
        addNode(otherId, String(firId), "fir");
        edges.push({ data: { id: `f${i}`, source: centerId, target: otherId, label: "linked to", direct: true } });
      });
    }

    // The suspect's own handsets, plus who those handsets call — this is what
    // turns the person view from a list of FIRs into an actual network.
    if (Array.isArray(network.phones)) {
      (network.phones as string[]).forEach((phone, i) => {
        const phoneId = `phone:${phone}`;
        addNode(phoneId, String(phone), "phone");
        edges.push({ data: { id: `u${i}`, source: centerId, target: phoneId, label: "uses", direct: true } });
      });
    }

    if (Array.isArray(network.phone_contacts)) {
      (network.phone_contacts as Array<Record<string, unknown>>).forEach((c, i) => {
        const sourceId = `phone:${c.source}`;
        const targetId = `phone:${c.target}`;
        addNode(sourceId, String(c.source), "phone");
        addNode(targetId, String(c.target), "phone");
        edges.push({
          data: {
            id: `c${i}`,
            source: sourceId,
            target: targetId,
            label: `${c.calls} calls`,
            direct: true,
          },
        });
      });
    }
  }

  return { nodes, edges };
}
