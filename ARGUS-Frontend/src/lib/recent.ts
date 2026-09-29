// Per-browser convenience only; nothing here is sent to the server.
const KEY = "argus.recentSearches";
const MAX = 6;

export function getRecentSearches(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(0, MAX) : [];
  } catch {
    return [];
  }
}

export function rememberSearch(query: string): void {
  const q = query.trim();
  if (!q) return;
  try {
    const next = [q, ...getRecentSearches().filter((x) => x.toLowerCase() !== q.toLowerCase())].slice(0, MAX);
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable (private mode) — recents are optional */
  }
}

export function clearRecentSearches(): void {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}
