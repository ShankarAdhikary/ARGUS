import type { PatternRecord } from "../types";

/** Red pill shown only for patterns whose FIRs are actually trafficking / exploitation-of-persons cases. */
export default function WomenSafetyBadge({ pattern, showTier = true }: { pattern: Pick<PatternRecord, "women_safety_flag" | "women_safety_fir_count" | "risk_tier">; showTier?: boolean }) {
  if (!pattern.women_safety_flag) return null;
  const count = pattern.women_safety_fir_count;
  return (
    <span className="ws-badges">
      <span
        className="ws-badge"
        title={`${count ?? "Several"} of the linked FIRs are trafficking / exploitation-of-persons cases`}
      >
        <span aria-hidden="true">⚠</span> Women Safety
      </span>
      {showTier && pattern.risk_tier && (
        <span className={`tier-pill tier-${pattern.risk_tier.toLowerCase()}`}>{pattern.risk_tier} risk</span>
      )}
    </span>
  );
}
