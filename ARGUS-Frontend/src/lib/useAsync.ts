import { useCallback, useEffect, useRef, useState } from "react";

/** Load independently of other requests so one slow endpoint never blanks a whole page. */
export function useAsync<T>(loader: () => Promise<T>): { data: T | null; loading: boolean; error: string; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const run = useCallback(() => {
    setLoading(true);
    setError("");
    loaderRef.current()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(run, [run]);
  return { data, loading, error, reload: run };
}
