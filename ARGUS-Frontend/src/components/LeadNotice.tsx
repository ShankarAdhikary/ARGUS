export default function LeadNotice({ children }: { children?: string }) {
  return (
    <p className="lead-notice" role="note">
      <strong>Investigative lead — verify before use.</strong>{" "}
      {children ?? "AI-derived relationships and scores are not evidence. Corroborate against source records."}
    </p>
  );
}
