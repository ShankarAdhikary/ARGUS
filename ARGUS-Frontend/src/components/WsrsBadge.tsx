import type { WsrsTier } from "../types";
import { TKey, useT } from "../i18n";

/** Women Safety Risk Score badge: score plus tier, coloured by tier. Replaces the plain "Women Safety" flag. */
export default function WsrsBadge({ total, tier }: { total: number; tier: WsrsTier }) {
  const t = useT();
  const tierLabel = t(`wsrs.${tier.toLowerCase()}` as TKey);
  return (
    <span className={`wsrs-badge wsrs-${tier.toLowerCase()}`} title={`WSRS ${total.toFixed(1)} of 100 — ${tierLabel} (${t("lead_notice.text")})`}>
      <span aria-hidden="true">⚠</span> WSRS {total.toFixed(0)} · {tierLabel}
    </span>
  );
}
