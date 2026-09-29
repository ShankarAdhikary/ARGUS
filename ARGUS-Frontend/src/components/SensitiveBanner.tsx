export default function SensitiveBanner({ reason }: { reason?: string | null }) {
  return (
    <div className="sensitive-banner" role="alert">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 1L2 13h12L8 1z"/><path d="M8 6v3M8 11h.01"/>
      </svg>
      <div>
        <strong>Access-restricted case.</strong> Your access is logged in the tamper-evident audit trail.
        {reason && <span> Reason: {reason}.</span>}
      </div>
    </div>
  );
}
