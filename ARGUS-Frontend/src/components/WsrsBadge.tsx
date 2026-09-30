import type { WsrsTier } from "../types";

/** Women Safety Risk Score badge: score plus tier, coloured by tier. Replaces the plain "Women Safety" flag. */
export default function WsrsBadge({ total, tier }: { total: number; tier: WsrsTier }) {
  return (
    <span className={`wsrs-badge wsrs-${tier.toLowerCase()}`} title={`WSRS ${total.toFixed(1)} of 100 — ${tier} risk (investigative lead)`}>
      <span aria-hidden="true">⚠</span> WSRS {total.toFixed(0)} · {tier}
    </span>
  );
}
