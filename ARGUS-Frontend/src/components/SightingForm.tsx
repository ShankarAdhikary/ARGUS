import { FormEvent, useState } from "react";
import { ApiError, logSighting } from "../lib/api";
import type { SightingAlert } from "../lib/api";
import { useT } from "../i18n";

/** `datetime-local` value (no zone) for "now", used as the upper bound: a sighting cannot be in the future. */
function nowLocal(): string {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function SightingForm({ initialSuspect, onLogged, onClose }: { initialSuspect: string; onLogged: () => void; onClose: () => void }) {
  const t = useT();
  const [suspect, setSuspect] = useState(initialSuspect);
  const [camera, setCamera] = useState("");
  const [zone, setZone] = useState("");
  const [seenAt, setSeenAt] = useState(nowLocal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<SightingAlert[] | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setDone(null);
    try {
      // The browser reads the picker in the officer's local time; toISOString() turns that into an unambiguous UTC instant.
      const res = await logSighting({ suspect_name: suspect.trim(), camera_id: camera.trim(), zone: zone.trim(), timestamp: new Date(seenAt).toISOString() });
      setDone(res.alerts);
      setCamera("");
      setZone("");
      onLogged();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404 ? t("network.sighting_unknown") : err instanceof Error ? err.message : "Could not record the sighting.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} aria-label={t("network.log_sighting")} style={{ marginTop: 8 }}>
      <p className="hint" style={{ margin: "0 0 8px" }}>{t("network.sighting_hint")}</p>
      <label>{t("network.sighting_suspect")} *
        <input value={suspect} onChange={(e) => setSuspect(e.target.value)} required maxLength={200} disabled={busy} />
      </label>
      <label>{t("network.sighting_camera")} *
        <input value={camera} onChange={(e) => setCamera(e.target.value)} required maxLength={100} disabled={busy} placeholder="CAM-07" />
      </label>
      <label>{t("network.sighting_zone")} *
        <input value={zone} onChange={(e) => setZone(e.target.value)} required maxLength={200} disabled={busy} />
      </label>
      <label>{t("network.sighting_time")} *
        <input type="datetime-local" value={seenAt} max={nowLocal()} onChange={(e) => setSeenAt(e.target.value)} required disabled={busy} />
      </label>
      <div className="page-actions" style={{ marginTop: 4 }}>
        <button disabled={busy || !suspect.trim() || !camera.trim() || !zone.trim() || !seenAt}>{busy ? t("common.loading") : t("common.submit")}</button>
        <button type="button" className="secondary" onClick={onClose}>{t("common.close")}</button>
      </div>
      {error && <p className="error" role="alert" style={{ marginTop: 8 }}>{error}</p>}
      {done && (
        <div role="status" style={{ marginTop: 8 }}>
          <p className="success-msg" style={{ margin: 0 }}>{t("network.sighting_recorded")}</p>
          {done.length > 0 && (
            <>
              <p className="hint" style={{ margin: "6px 0 2px" }}>{t("network.sighting_alerts")}</p>
              <ul className="hint" style={{ margin: 0, paddingLeft: 18 }}>{done.map((a, i) => <li key={i}>{a.explanation}</li>)}</ul>
            </>
          )}
        </div>
      )}
    </form>
  );
}
