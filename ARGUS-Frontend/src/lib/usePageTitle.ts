import { useEffect } from "react";

/** Give every route its own browser-tab / screen-reader title. */
export function usePageTitle(title: string): void {
  useEffect(() => {
    const previous = document.title;
    document.title = title ? `${title} · ARGUS` : "ARGUS";
    return () => { document.title = previous; };
  }, [title]);
}
