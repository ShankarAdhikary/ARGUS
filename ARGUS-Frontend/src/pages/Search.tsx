import { FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import EntityCard from "../components/EntityCard";
import { entityTypeGuess, resolveCheck, searchFirs } from "../lib/api";
import { rememberSearch } from "../lib/recent";
import type { ApiRecord } from "../types";
import { useT } from "../i18n";

type FilterType = "all" | "person" | "phone" | "fir";
type ViewMode = "grid" | "table";

export default function Search() {
  const t = useT();
  const [params, setParams] = useSearchParams();
  const initial = params.get("q") ?? "";
  const [query, setQuery]         = useState(initial);
  const [filter, setFilter]       = useState<FilterType>("all");
  const [viewMode, setViewMode]   = useState<ViewMode>("grid");
  const [results, setResults]     = useState<ApiRecord[]>([]);
  const [suggestions, setSuggestions] = useState<Array<{ candidate: string; similarity: number }>>([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");
  const [searched, setSearched]   = useState(false);
  const [total, setTotal]         = useState(0);

  async function runSearch(q: string) {
    if (!q.trim()) return;
    setLoading(true); setError(""); setSuggestions([]);
    try {
      rememberSearch(q);
      const response = await searchFirs(q);
      setResults(response.results ?? []);
      setTotal(response.count ?? 0);
      setSearched(true);
      if (!response.results?.length) {
        const candidates = await resolveCheck(q);
        setSuggestions(candidates);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { if (initial) void runSearch(initial); }, []); // eslint-disable-line

  function submit(e: FormEvent) {
    e.preventDefault();
    setParams(query ? { q: query } : {});
    void runSearch(query);
  }

  const filtered = results.filter((r) => {
    if (filter === "all") return true;
    if (filter === "person") return typeof r.accused === "string";
    if (filter === "fir") return typeof r.fir_id === "string";
    return true;
  });

  return (
    <main>
      <header className="page-header">
        <div className="page-header-left">
          <span className="eyebrow">{t("search.eyebrow")}</span>
          <h1>{t("search.title")}</h1>
          <p className="page-subtitle">
            Search across FIRs, CDRs, financial records and surveillance. Fuzzy matching and alias detection included.
          </p>
        </div>
      </header>

      {/* Search bar */}
      <div className="card sticky-filter" style={{ marginBottom: 16 }}>
        <form className="inline-form" onSubmit={submit}>
          <input
            aria-label={t("search.title")} placeholder={t("search.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
            style={{ fontSize: "0.9rem", padding: "11px 14px" }}
          />
          <button disabled={loading}>
            {loading ? t("common.loading") : t("common.search")}
          </button>
        </form>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          <div className="filter-chips" style={{ marginTop: 0 }}>
            {(["all", "person", "phone", "fir"] as FilterType[]).map((f) => (
              <button key={f} type="button" className={`chip ${filter === f ? "chip-active" : ""}`} onClick={() => setFilter(f)}>
                {f === "all" ? t("common.all") : f === "person" ? t("search.suspects") : f === "phone" ? t("search.phones") : t("search.firs")}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {(["grid", "table"] as ViewMode[]).map((m) => (
              <button key={m} type="button" className={`chip ${viewMode === m ? "chip-active" : ""}`}
                style={{ padding: "3px 10px" }} onClick={() => setViewMode(m)}>
                {m === "grid" ? "⊞ Grid" : "☰ Table"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && <p className="error" role="alert" style={{ marginBottom: 12 }}>{error}</p>}

      {searched && !loading && (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <p className="hint">
            {filtered.length > 0
              ? `${filtered.length} result${filtered.length === 1 ? "" : "s"}${filter !== "all" ? ` (filtered from ${total})` : ""} for "${query}"`
              : `No results for "${query}".`}
          </p>
        </div>
      )}

      {/* Fuzzy suggestions */}
      {suggestions.length > 0 && (
        <div className="card" style={{ marginBottom: 14 }}>
          <span className="section-label">Did you mean…</span>
          <div className="filter-chips">
            {suggestions.slice(0, 6).map((s) => (
              <button key={s.candidate} type="button" className="chip"
                onClick={() => { setQuery(s.candidate); void runSearch(s.candidate); }}>
                {s.candidate}
                <span style={{ marginLeft: 4, fontWeight: 400, opacity: .65 }}>({Math.round(s.similarity * 100)}%)</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {loading && (
        <div className="page-loading">
          <span className="spinner" /> Searching across all data sources…
        </div>
      )}

      {/* Grid view */}
      {viewMode === "grid" && (
        <div className="result-grid">
          {filtered.map((r, i) => {
            const value = typeof r.accused === "string" ? r.accused
              : typeof r.fir_id === "string" ? String(r.fir_id)
              : `record-${i}`;
            const type = entityTypeGuess(value === `record-${i}` ? "" : value);
            return (
              <EntityCard
                key={`${value}-${i}`}
                type={typeof r.fir_id === "string" && !r.accused ? "fir" : type}
                value={typeof r.accused === "string" ? r.accused : String(r.fir_id ?? "Unknown")}
                subtitle={typeof r.description === "string" ? r.description.slice(0, 90)
                  : typeof r.station === "string" ? r.station : undefined}
                sourceCount={1}
                citation={typeof r.fir_id === "string" ? `FIR ${r.fir_id}` : undefined}
                date={typeof r.date === "string" ? r.date : undefined}
              />
            );
          })}
        </div>
      )}

      {/* Table view */}
      {viewMode === "table" && filtered.length > 0 && (
        <div className="audit-scroll" style={{ marginTop: 6 }}>
          <table className="audit-table">
            <thead>
              <tr>
                <th>{t("hunt.fir_id")}</th>
                <th>Accused</th>
                <th>Station</th>
                <th>{t("common.date")}</th>
                <th>Network</th>
                <th>{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={i}>
                  <td>{typeof r.fir_id === "string" ? r.fir_id : "—"}</td>
                  <td style={{ fontWeight: 600, color: "var(--accent-2)" }}>
                    {typeof r.accused === "string" ? r.accused : "—"}
                  </td>
                  <td>{typeof r.station === "string" ? r.station : "—"}</td>
                  <td>{typeof r.date === "string" ? r.date : "—"}</td>
                  <td>
                    {typeof r.network === "string" ? (
                      <span style={{ textTransform: "capitalize", fontSize: "0.75rem" }}>{r.network}</span>
                    ) : "—"}
                  </td>
                  <td>
                    {typeof r.accused === "string" && (
                      <Link
                        to={`/network?focus=${encodeURIComponent(r.accused as string)}&type=person`}
                        style={{ fontSize: "0.75rem", color: "var(--accent-2)" }}
                      >
                        Graph →
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
