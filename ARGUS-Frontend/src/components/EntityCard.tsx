import { Link } from "react-router-dom";
import type { EntityType } from "../types";
import SourceChip from "./SourceChip";
import ConfidencePill from "./ConfidencePill";

interface Props {
  type: EntityType;
  value: string;
  subtitle?: string;
  sourceCount?: number;
  citation?: string;
  confidence?: number;
  date?: string;
}

const TYPE_LABELS: Partial<Record<EntityType, string>> = {
  person: "Suspect / Person",
  phone: "Phone number",
  fir: "FIR",
  financial_account: "Financial account",
  vehicle: "Vehicle",
  organization: "Organization",
  location: "Location",
  event: "Event",
};

export default function EntityCard({ type, value, subtitle, sourceCount, citation, confidence, date }: Props) {
  return (
    <div className="entity-card">
      <div className="entity-card-top">
        <span className={`type-dot type-${type}`} style={{ marginTop: 7 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 style={{ wordBreak: "break-word" }}>{value}</h3>
          {subtitle && <p className="entity-subtitle">{subtitle}</p>}
        </div>
        {confidence !== undefined && <ConfidencePill confidence={confidence} />}
      </div>

      <div className="entity-card-meta">
        <span className="type-label">{TYPE_LABELS[type] ?? type.replace("_", " ")}</span>
        {typeof sourceCount === "number" && (
          <span>{sourceCount} source{sourceCount === 1 ? "" : "s"}</span>
        )}
        {citation && <SourceChip label={citation} />}
        {date && <span style={{ color: "var(--text-3)", fontSize: "0.75rem" }}>{date}</span>}
      </div>

      <div className="entity-card-actions">
        <Link to={`/entity/${type}/${encodeURIComponent(value)}`}>View profile →</Link>
        <Link to={`/network?focus=${encodeURIComponent(value)}&type=${type}`}>Explore network →</Link>
      </div>
    </div>
  );
}
