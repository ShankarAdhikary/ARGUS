import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import LeadNotice from "../components/LeadNotice";
import SensitiveBanner from "../components/SensitiveBanner";
import NetworkGraph from "../components/NetworkGraph";
import { addCaseNote, auditLog, getCase, logAudit, networkAccused, networkPhone } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { AuditEntry, CaseDetail, EntityType, GraphElements } from "../types";

type Tab = "overview" | "network" | "report" | "access";

export default function CaseWorkspace() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [caseRecord, setCaseRecord] = useState<CaseDetail | null>(null);
  const [needsJustification, setNeedsJustification] = useState(false);
  const [justification, setJustification] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const [note, setNote] = useState("");
  const [graph, setGraph] = useState<GraphElements>({ nodes: [], edges: [] });
  const [access, setAccess] = useState<AuditEntry[]>([]);

  async function load(withJustification?: string) {
    setError("");
    try {
      const justificationToSend =
        withJustification ?? sessionStorage.getItem(`justification:${id}`) ?? undefined;
      const record = await getCase(id, justificationToSend);
      setCaseRecord(record);
      setNeedsJustification(false);
      if (user) logAudit("view_case", record.title, user, justificationToSend);
    } catch (err) {
      if (err instanceof Error && err.message.toLowerCase().includes("justif")) {
        setNeedsJustification(true);
      } else {
        setError(err instanceof Error ? err.message : "Could not load case.");
      }
    }
  }

  useEffect(() => {
    load();
    // Admin-only endpoint: don't call it for other roles.
    if (user?.role === "admin") auditLog().then(setAccess).catch(() => setAccess([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Covers the case where the record loads but is flagged sensitive and no
  // justification has been given yet; the server-side 428 is handled in load().
  useEffect(() => {
    if (caseRecord?.is_sensitive && !justification && !sessionStorage.getItem(`justified:${id}`)) {
      setNeedsJustification(true);
    }
  }, [caseRecord, id, justification]);

  async function confirmJustification(event: FormEvent) {
    event.preventDefault();
    if (!justification.trim()) return;
    sessionStorage.setItem(`justified:${id}`, "1");
    sessionStorage.setItem(`justification:${id}`, justification);
    // Re-fetch with the justification: the first request was refused with 428,
    // so the record still has to be retrieved before the workspace can render.
    await load(justification);
    if (user) logAudit("access_sensitive_case", caseRecord?.title ?? id, user, justification);
  }

  useEffect(() => {
    if (!caseRecord || tab !== "network") return;
    const primary = caseRecord.entities[0];
    if (!primary) return;
    const storedJustification = sessionStorage.getItem(`justification:${id}`) ?? undefined;
    const call = primary.entity_type === "phone"
      ? networkPhone(primary.entity_value, caseRecord.case_id, storedJustification)
      : networkAccused(primary.entity_value, caseRecord.case_id, storedJustification);
    call.then((network) => setGraph(buildGraph(caseRecord.entities, network as Record<string, unknown>)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseRecord, tab]);

  async function addNote(event: FormEvent) {
    event.preventDefault();
    if (!note.trim() || !user || !caseRecord) return;
    const updated = await addCaseNote(caseRecord.case_id, note, user.full_name);
    setCaseRecord(updated);
    setNote("");
  }

  if (error) return <main><p className="error">{error}</p></main>;

  // The gate is checked before the loading guard: a 428 means the record was
  // never returned, so waiting for `caseRecord` here would hang on "Loading".
  if (needsJustification) {
    return (
      <main>
        <PageHeader eyebrow="Access restricted" title={caseRecord?.title ?? "Sensitive case"} />
        <section className="card" style={{ maxWidth: 520 }}>
          <SensitiveBanner reason={caseRecord?.sensitivity_reason} />
          <p className="hint">This case is flagged sensitive. State your investigative justification to proceed — this is recorded in the audit trail.</p>
          <form onSubmit={confirmJustification}>
            <label>Justification<textarea rows={3} value={justification} onChange={(e) => setJustification(e.target.value)} required /></label>
            <button>Proceed &amp; log access</button>
          </form>
        </section>
      </main>
    );
  }

  if (!caseRecord) return <main><p className="hint">Loading case…</p></main>;

  return (
    <main>
      <PageHeader
        eyebrow={`${caseRecord.fir_number} · ${caseRecord.jurisdiction}`}
        title={caseRecord.title}
        actions={<Link className="btn-link" to={`/cases/${caseRecord.case_id}/report`}>Generate report →</Link>}
      >
        <span className={`status-pill status-${caseRecord.status}`}>{caseRecord.status.replace("_", " ")}</span>
      </PageHeader>
      <LeadNotice />

      {caseRecord.is_sensitive && <SensitiveBanner reason={caseRecord.sensitivity_reason} />}

      <div className="tab-bar">
        {(["overview", "network", "report", "access"] as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? "tab-active" : ""}`} onClick={() => setTab(t)}>{t === "report" ? "report builder" : t === "access" ? "access log" : t}</button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid-2">
          <section className="card">
            <p className="section-label">Pinned entities</p>
            <div className="case-list">
              {caseRecord.entities.map((e) => (
                <Link key={e.entity_value} to={`/entity/${e.entity_type}/${encodeURIComponent(e.entity_value)}`} className="case-row">
                  <span className={`type-dot type-${e.entity_type}`} />
                  <strong>{e.entity_value}</strong>
                  <span className="hint">{e.entity_type}</span>
                </Link>
              ))}
              {!caseRecord.entities.length && <p className="hint">No entities pinned yet — pin from search or entity detail.</p>}
            </div>
          </section>
          <section className="card">
            <p className="section-label">Notes</p>
            <div className="note-list">
              {caseRecord.notes.map((n) => (
                <div key={n.note_id} className="note-item">
                  <p>{n.content}</p>
                  <span className="hint">{n.author} &middot; {new Date(n.created_at).toLocaleString()}</span>
                </div>
              ))}
              {!caseRecord.notes.length && <p className="hint">No notes yet.</p>}
            </div>
            <form className="inline-form" onSubmit={addNote} style={{ marginTop: 12 }}>
              <input aria-label="Investigative note" placeholder="Add an investigative note…" value={note} onChange={(e) => setNote(e.target.value)} />
              <button>Add</button>
            </form>
          </section>
        </div>
      )}

      {tab === "network" && (
        <section className="card">
          {graph.nodes.length ? <NetworkGraph elements={graph} height={420} /> : <p className="hint">Pin an entity to see its network here.</p>}
        </section>
      )}

      {tab === "report" && (
        <section className="card">
          <p className="hint">Open the full Report Builder to select sections and export.</p>
          <Link className="btn-link" to={`/cases/${caseRecord.case_id}/report`}>Go to Report Builder →</Link>
        </section>
      )}

      {tab === "access" && (
        <section className="card">
          <table className="audit-table">
            <thead><tr><th>User</th><th>Action</th><th>Resource</th><th>Justification</th><th>When</th></tr></thead>
            <tbody>
              {access.filter((a) => a.resource.includes(caseRecord.title)).map((a) => (
                <tr key={a.audit_id}>
                  <td>{a.user}</td><td>{a.action}</td><td>{a.resource}</td><td>{a.justification ?? "—"}</td>
                  <td>{new Date(a.occurred_at).toLocaleString()}</td>
                </tr>
              ))}
              {!access.length && <tr><td colSpan={5} className="hint">No access recorded yet this session.</td></tr>}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

function buildGraph(entities: CaseDetail["entities"], network: Record<string, unknown>): GraphElements {
  const nodes: GraphElements["nodes"] = entities.map((e) => ({ data: { id: `${e.entity_type}:${e.entity_value}`, label: e.entity_value, type: e.entity_type as EntityType } }));
  const edges: GraphElements["edges"] = [];
  const primary = entities[0];
  if (!primary) return { nodes, edges };
  const centerId = `${primary.entity_type}:${primary.entity_value}`;

  if (Array.isArray(network.phones)) {
    (network.phones as string[]).forEach((phone, i) => {
      const phoneId = `phone:${phone}`;
      if (!nodes.find((n) => n.data.id === phoneId)) {
        nodes.push({ data: { id: phoneId, label: phone, type: "phone" } });
      }
      edges.push({ data: { id: `pe${i}`, source: centerId, target: phoneId, label: "uses", direct: true } });
    });
  }
  if (Array.isArray(network.phone_contacts)) {
    (network.phone_contacts as Array<Record<string, unknown>>).forEach((contact, i) => {
      const source = `phone:${String(contact.source)}`;
      const target = `phone:${String(contact.target)}`;
      if (!nodes.find((n) => n.data.id === source)) {
        nodes.push({ data: { id: source, label: String(contact.source), type: "phone" } });
      }
      if (!nodes.find((n) => n.data.id === target)) {
        nodes.push({ data: { id: target, label: String(contact.target), type: "phone" } });
      }
      edges.push({ data: { id: `pc${i}`, source, target, label: "called", direct: true } });
    });
  }
  if (Array.isArray(network.connections)) {
    (network.connections as Array<Record<string, unknown>>).forEach((c, i) => {
      const otherId = `phone:${c.connected_phone}`;
      if (!nodes.find((n) => n.data.id === otherId)) nodes.push({ data: { id: otherId, label: String(c.connected_phone), type: "phone" } });
      edges.push({ data: { id: `ce${i}`, source: centerId, target: otherId, label: "called", direct: true } });
    });
  }
  if (Array.isArray(network.linked_firs)) {
    (network.linked_firs as string[]).forEach((firId, i) => {
      const otherId = `fir:${firId}`;
      if (!nodes.find((n) => n.data.id === otherId)) nodes.push({ data: { id: otherId, label: String(firId), type: "fir" } });
      edges.push({ data: { id: `fe${i}`, source: centerId, target: otherId, label: "linked to", direct: true } });
    });
  }
  return { nodes, edges };
}
