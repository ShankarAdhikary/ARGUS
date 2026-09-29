import type { ConfidenceTier } from "../types";

export function tierFor(confidence: number): ConfidenceTier {
  if (confidence >= 0.75) return "high";
  if (confidence >= 0.5) return "medium";
  return "low";
}

const LABEL: Record<ConfidenceTier, string> = { high: "High", medium: "Medium", low: "Low" };

export default function ConfidencePill({ confidence }: { confidence: number }) {
  const tier = tierFor(confidence);
  return (
    <span className={`pill pill-${tier}`}>
      <span className="pill-dot" /> {LABEL[tier]} {Math.round(confidence * 100)}%
    </span>
  );
}
