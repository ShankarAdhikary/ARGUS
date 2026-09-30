import { personWsrs } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import ErrorBoundary from "./ErrorBoundary";
import Skeleton from "./Skeleton";
import WsrsBadge from "./WsrsBadge";
import WsrsBreakdown from "./WsrsBreakdown";

function Inner({ person }: { person: string }) {
  const wsrs = useAsync(() => personWsrs(person));
  const result = wsrs.data?.wsrs ?? null;
  if (wsrs.loading && !wsrs.data) return <section className="card"><Skeleton rows={4} /></section>;
  if (wsrs.error) {
    return (
      <div className="panel-error" role="alert" style={{ marginBottom: 18 }}>
        <span>Couldn't load the Women Safety Risk Score: {wsrs.error}</span>
        <button type="button" className="link-btn" onClick={wsrs.reload}>Retry</button>
      </div>
    );
  }
  if (!result) return null; // no women-safety history: show nothing rather than a zero score
  return (
    <section className="card" style={{ marginBottom: 18 }} aria-labelledby="wsrs-h">
      <div className="card-title">
        <h2 id="wsrs-h" className="card-heading">Women Safety Risk Score</h2>
        <WsrsBadge total={result.total} tier={result.tier} />
      </div>
      <WsrsBreakdown breakdown={result} />
      <p className="lead-note">
        Investigative lead — verify before use. Confidence {Math.round(result.confidence * 100)}%. {result.explanation}
      </p>
    </section>
  );
}

export default function WsrsProfile({ person }: { person: string }) {
  return (
    <ErrorBoundary label="the risk score">
      <Inner person={person} />
    </ErrorBoundary>
  );
}
