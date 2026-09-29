import { useState } from "react";

export default function RecoveryCodes({ codes }: { codes: string[] }) {
  const [copied, setCopied] = useState(false);
  const text = codes.join("\n");
  function download() {
    const url = URL.createObjectURL(new Blob([`ARGUS recovery codes (each works once)\n\n${text}\n`], { type: "text/plain" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "argus-recovery-codes.txt" });
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div>
      <ul className="recovery-grid" aria-label="Recovery codes">
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ul>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button type="button" className="secondary" onClick={() => { void navigator.clipboard?.writeText(text).then(() => setCopied(true)); }}>{copied ? "Copied" : "Copy"}</button>
        <button type="button" className="secondary" onClick={download}>Download</button>
      </div>
    </div>
  );
}
