import type { WsrsBreakdown as Breakdown } from "../types";
import { TKey, useT } from "../i18n";

const ORDER: { key: keyof Breakdown["factors"]; name: TKey }[] = [
  { key: "recency", name: "wsrs.factors.recency" },
  { key: "repeat", name: "wsrs.factors.repeat" },
  { key: "escalation", name: "wsrs.factors.escalation" },
  { key: "network", name: "wsrs.factors.network" },
  { key: "geographic", name: "wsrs.factors.geographic" },
];

/** Horizontal bar per WSRS factor, with the reason for each score. */
export default function WsrsBreakdown({ breakdown }: { breakdown: Breakdown }) {
  const t = useT();
  return (
    <div className="wsrs-bars" role="list" aria-label="Women Safety Risk Score factors">
      {ORDER.map(({ key, name: nameKey }) => {
        const name = t(nameKey);
        const f = breakdown.factors[key];
        const weight = Math.round((breakdown.weights?.[key] ?? 0) * 100);
        return (
          <div key={key} className="wsrs-bar-row" role="listitem">
            <div className="wsrs-bar-head">
              <span>{name}{weight ? <span className="hint"> · weight {weight}%</span> : null}</span>
              <span className="wsrs-bar-score">{f.score}</span>
            </div>
            <div
              className="wsrs-bar-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={f.score}
              aria-label={`${name} score`}
            >
              <div className="wsrs-bar-fill" style={{ width: `${Math.min(Math.max(f.score, 0), 100)}%` }} />
            </div>
            <span className="hint">{f.label}</span>
          </div>
        );
      })}
    </div>
  );
}
