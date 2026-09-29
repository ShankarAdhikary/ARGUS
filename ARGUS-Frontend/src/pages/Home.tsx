import { Link } from "react-router-dom";
import { usePageTitle } from "../lib/usePageTitle";

const STEPS = [
  { n: "01", title: "Ingest", desc: "Bring in FIRs, call-detail records, bank and UPI transactions, surveillance reports and photographs — files or free text." },
  { n: "02", title: "Extract & resolve", desc: "Entities are pulled from messy narratives, then near-duplicate identities are flagged for an analyst to confirm. Nothing merges on its own." },
  { n: "03", title: "Map & detect", desc: "Everything lands in one graph. Burner phones, structuring and network hubs are surfaced with a confidence score and the evidence behind it." },
  { n: "04", title: "Investigate & report", desc: "Pin entities to a case, add notes, and export a cited, confidence-tagged PDF for review." },
];

const CAPABILITIES = [
  { name: "Network explorer", desc: "Trace calls, transactions and shared FIRs between people, phones and accounts, with date-bounded snapshots and shortest-path search." },
  { name: "Influencer ranking", desc: "PageRank and betweenness point to likely organisers rather than peripheral actors." },
  { name: "Pattern detection", desc: "Burner-phone clusters, financial structuring and community detection, each explained in plain language." },
  { name: "Alias & entity resolution", desc: "Fuzzy name and phone matching surfaces a repeat offender hiding behind a different spelling." },
  { name: "Biometric hunt", desc: "Match a face against enrolled suspects and jump straight to the linked FIR." },
  { name: "Watchlist alerts", desc: "Get notified the moment a watched person, phone or account appears in new records." },
];

const SAFEGUARDS = [
  { name: "A lead, never a verdict", desc: "Every AI-derived link is labelled an investigative lead and carries a confidence score and a source. People make the decisions." },
  { name: "Tamper-evident audit", desc: "Each read, write and export is recorded in a hash-chained, append-only log." },
  { name: "Sensitive-case gating", desc: "Cases involving minors or survivors require a stated justification before anyone can open them." },
  { name: "Jurisdiction scoping", desc: "Investigators see the cases of their own jurisdiction; wider access is a role, not a default." },
];

function NetworkArt() {
  const nodes = [
    [230, 60], [120, 120], [340, 110], [70, 230], [210, 190], [330, 240], [150, 320], [290, 350], [400, 180], [40, 120],
  ];
  const edges = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 4], [2, 5], [2, 8], [4, 5], [4, 6], [5, 7], [6, 7], [3, 6], [9, 1], [8, 5]];
  return (
    <svg viewBox="0 0 440 400" role="img" aria-label="Illustration of a network graph" className="home-art">
      {edges.map(([a, b], i) => (
        <line key={i} x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]} className={i % 4 === 0 ? "art-edge dashed" : "art-edge"} />
      ))}
      {nodes.map(([x, y], i) => (
        <g key={i}>
          {i === 4 && <circle cx={x} cy={y} r="26" className="art-halo" />}
          <circle cx={x} cy={y} r={i === 4 ? 12 : i % 3 === 0 ? 8 : 6} className={i === 4 ? "art-node hub" : i % 3 === 0 ? "art-node phone" : "art-node"} />
        </g>
      ))}
    </svg>
  );
}

export default function Home() {
  usePageTitle("");
  return (
    <div className="home">
      <header className="home-bar">
        <Link to="/" className="home-brand" aria-label="ARGUS home">
          <span className="home-logo" aria-hidden="true" />
          <span>ARGUS</span>
        </Link>
        <nav className="home-nav" aria-label="Sections">
          <a href="#how">How it works</a>
          <a href="#capabilities">Capabilities</a>
          <a href="#safeguards">Safeguards</a>
          <Link to="/login" className="home-signin">Sign in</Link>
        </nav>
      </header>

      <section className="home-hero">
        <div className="home-hero-text">
          <span className="home-eyebrow">Ministry of Home Affairs · NCRB · Women Safety Division</span>
          <h1>See the network behind the case.</h1>
          <p className="home-lede">
            ARGUS fuses FIRs, call records, financial transactions and surveillance into one graph, so an
            investigator can find the people connecting a case in hours instead of weeks — with every link
            scored, sourced and auditable.
          </p>
          <div className="home-cta">
            <Link to="/login" className="home-btn primary">Sign in to ARGUS →</Link>
            <a href="#how" className="home-btn">How it works</a>
          </div>
          <p className="home-note">Authorised personnel only. All activity is logged.</p>
        </div>
        <NetworkArt />
      </section>

      <section id="how" className="home-section">
        <span className="home-eyebrow">How it works</span>
        <h2>From scattered records to a case you can act on</h2>
        <ol className="home-steps">
          {STEPS.map((s) => (
            <li key={s.n}>
              <span className="home-step-n">{s.n}</span>
              <h3>{s.title}</h3>
              <p>{s.desc}</p>
            </li>
          ))}
        </ol>
      </section>

      <section id="capabilities" className="home-section">
        <span className="home-eyebrow">Capabilities</span>
        <h2>Built for how investigators actually work</h2>
        <div className="home-grid">
          {CAPABILITIES.map((c) => (
            <div key={c.name} className="home-card">
              <h3>{c.name}</h3>
              <p>{c.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="safeguards" className="home-section">
        <span className="home-eyebrow">Safeguards</span>
        <h2>An investigative aid, not an autonomous decision-maker</h2>
        <div className="home-grid two">
          {SAFEGUARDS.map((c) => (
            <div key={c.name} className="home-card">
              <h3>{c.name}</h3>
              <p>{c.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="home-final">
        <h2>Ready to open a case?</h2>
        <Link to="/login" className="home-btn primary">Sign in →</Link>
      </section>

      <footer className="home-foot">
        Investigative aid only. AI-derived insights are leads to be verified against source records; final determination rests
        with the investigating officer. Unauthorised access is an offence under the IT Act, 2000.
      </footer>
    </div>
  );
}
