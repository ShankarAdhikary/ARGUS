import { useT } from "../i18n";
export default function LeadNotice({ children }: { children?: string }) {
  const t = useT();
  return (
    <p className="lead-notice" role="note">
      <strong>{t("lead_notice.text")}</strong>{" "}
      {children ?? "AI-derived relationships and scores are not evidence. Corroborate against source records."}
    </p>
  );
}
