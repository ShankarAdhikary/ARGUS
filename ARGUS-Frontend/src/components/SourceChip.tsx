export default function SourceChip({ label, onClick }: { label: string; onClick?: () => void }) {
  const Tag = onClick ? "button" : "span";
  return (
    <Tag className="source-chip" onClick={onClick} type={onClick ? "button" : undefined}>
      {label}
    </Tag>
  );
}
