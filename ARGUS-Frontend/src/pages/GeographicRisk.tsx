import { FormEvent, useMemo, useState } from "react";
import { GeoJSON, MapContainer, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import districts from "../assets/india_districts.geojson?url";
import ErrorBoundary from "../components/ErrorBoundary";
import LeadNotice from "../components/LeadNotice";
import PageHeader from "../components/PageHeader";
import Skeleton from "../components/Skeleton";
import { hotspots, riskForecast } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useAsync } from "../lib/useAsync";
import { usePageTitle } from "../lib/usePageTitle";
import type { FeatureCollection } from "geojson";

/** Leaflet paints with colour strings, so resolve the theme's CSS variables at render time (dark-mode safe). */
function themeColour(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function useDistrictLayer() {
  return useAsync<FeatureCollection>(() => fetch(districts).then((r) => {
    if (!r.ok) throw new Error(`District map failed to load (${r.status})`);
    return r.json();
  }));
}

function HotspotMap() {
  const hs = useAsync(() => hotspots("WOMEN_SAFETY"));
  const base = useDistrictLayer();
  const styles = useMemo(
    () => ({
      high: { color: themeColour("--red", "#ef4444"), fillOpacity: 0.55, weight: 1 },
      medium: { color: themeColour("--amber", "#f59e0b"), fillOpacity: 0.4, weight: 1 },
      low: { color: themeColour("--blue", "#38bdf8"), fillOpacity: 0.28, weight: 1 },
    }),
    []
  );
  const outline = useMemo(() => ({ color: themeColour("--border-2", "#888"), weight: 0.5, fillOpacity: 0 }), []);
  const empty = hs.data && hs.data.features.length === 0;

  return (
    <section className="card geo-map-card" aria-labelledby="geo-map-h">
      <div className="card-title">
        <h2 id="geo-map-h" className="card-heading">Women-safety FIR hotspots</h2>
        <span className="geo-legend" aria-label="Legend">
          {(["high", "medium", "low"] as const).map((l) => (
            <span key={l} className="chip"><span className="geo-swatch" style={{ background: styles[l].color }} /> {l}</span>
          ))}
        </span>
      </div>
      {(hs.loading && !hs.data) && <Skeleton rows={6} />}
      {hs.error && (
        <div className="panel-error" role="alert">
          <span>Couldn't load hotspots: {hs.error}</span>
          <button type="button" className="link-btn" onClick={hs.reload}>Retry</button>
        </div>
      )}
      {empty && <p className="hint">{hs.data?.properties.reason ?? "No geocoded women-safety FIRs yet."}</p>}
      <div className="geo-map" role="application" aria-label="Map of women-safety FIR density across India">
        <MapContainer center={[22.5, 79]} zoom={5} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
          <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
          {base.data && <GeoJSON data={base.data} style={() => outline} />}
          {hs.data && !empty && (
            // Render low → high so the densest regions end up on top.
            (["low", "medium", "high"] as const).map((level) => {
              const f = hs.data!.features.find((x) => x.properties.level === level);
              return f ? <GeoJSON key={`${level}-${hs.data!.properties.points}`} data={f as never} style={() => styles[level]} /> : null;
            })
          )}
        </MapContainer>
      </div>
      {hs.data && (
        <p className="lead-note">
          {hs.data.properties.points} geocoded FIRs · confidence {Math.round((hs.data.properties.confidence ?? 0) * 100)}%. {hs.data.properties.explanation}
        </p>
      )}
    </section>
  );
}

function ForecastPanel({ initial }: { initial: string }) {
  const [input, setInput] = useState(initial);
  const [target, setTarget] = useState(initial);
  const forecast = useAsync(() => (target ? riskForecast(target) : Promise.resolve(null)));
  // Re-run when the submitted jurisdiction changes.
  const [lastTarget, setLastTarget] = useState(target);
  if (lastTarget !== target) {
    setLastTarget(target);
    forecast.reload();
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    setTarget(input.trim());
  }
  return (
    <section className="card" aria-labelledby="geo-forecast-h">
      <h2 id="geo-forecast-h" className="card-heading">Risk forecast</h2>
      <form onSubmit={submit} className="quick-search-bar" role="search">
        <input aria-label="Jurisdiction" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Jurisdiction, e.g. Central District" />
        <button disabled={!input.trim()}>Forecast</button>
      </form>
      {forecast.loading && <Skeleton rows={4} />}
      {forecast.error && (
        <div className="panel-error" role="alert" style={{ marginTop: 10 }}>
          <span>{forecast.error}</span>
          <button type="button" className="link-btn" onClick={forecast.reload}>Retry</button>
        </div>
      )}
      {forecast.data && !forecast.error && (
        <>
          <p className="hint" style={{ margin: "10px 0" }}>
            {forecast.data.jurisdiction} baseline risk {forecast.data.source_risk} · confidence {Math.round(forecast.data.confidence * 100)}%
          </p>
          {forecast.data.forecast.length === 0 && <p className="hint">No neighbouring jurisdiction shares suspects with this one.</p>}
          <ol className="geo-forecast-list">
            {forecast.data.forecast.map((r) => (
              <li key={r.jurisdiction}>
                <div className="wsrs-bar-head">
                  <strong>{r.jurisdiction}</strong>
                  <span className="wsrs-bar-score">+{r.score}</span>
                </div>
                <div className="wsrs-bar-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={r.score} aria-label={`${r.jurisdiction} diffused risk`}>
                  <div className="wsrs-bar-fill" style={{ width: `${Math.min(r.score, 100)}%` }} />
                </div>
                <span className="hint">{r.explanation}</span>
              </li>
            ))}
          </ol>
          <p className="lead-note">Investigative lead — verify before use. {forecast.data.method}</p>
        </>
      )}
    </section>
  );
}

export default function GeographicRisk() {
  usePageTitle("Geographic risk");
  const { user } = useAuth();
  const canForecast = user?.role !== "investigator"; // the forecast names other jurisdictions
  return (
    <main>
      <PageHeader eyebrow="Intelligence" title="Geographic risk">
        Where women-safety FIRs concentrate, and which neighbouring jurisdictions may inherit risk.
      </PageHeader>
      <LeadNotice />
      <div className="geo-layout">
        <ErrorBoundary label="the hotspot map"><HotspotMap /></ErrorBoundary>
        <aside className="dash-col">
          {canForecast ? (
            <ErrorBoundary label="the forecast"><ForecastPanel initial={user?.jurisdiction ?? ""} /></ErrorBoundary>
          ) : (
            <section className="card"><p className="hint">The cross-jurisdiction forecast is available to analyst roles and above.</p></section>
          )}
        </aside>
      </div>
    </main>
  );
}
