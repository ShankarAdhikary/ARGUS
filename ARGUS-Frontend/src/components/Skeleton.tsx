/** Placeholder rows shown while a section loads. */
export default function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "4px 0" }} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton-row" style={{ opacity: 0.7 - i * 0.12 }} />
      ))}
    </div>
  );
}
