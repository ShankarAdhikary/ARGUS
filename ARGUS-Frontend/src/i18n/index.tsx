import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import en from "./en.json";
import hi from "./hi.json";

export type Lang = "en" | "hi";

type Flatten<T, P extends string = ""> = {
  [K in keyof T & string]: K extends `_${string}` ? never
    : T[K] extends string ? `${P}${K}`
    : Flatten<T[K], `${P}${K}.`>;
}[keyof T & string];
/** Every key in en.json, as a string-literal type, so a mistyped key fails the type-check. */
export type TKey = Flatten<typeof en>;

type Tree = { [k: string]: string | string[] | Tree };
const DICTS: Record<Lang, Tree> = { en: en as Tree, hi: hi as Tree };
const STORAGE_KEY = "argus.lang";

function lookup(tree: Tree, key: string): string | undefined {
  let node: string | string[] | Tree | undefined = tree;
  for (const part of key.split(".")) {
    if (node === undefined || typeof node === "string" || Array.isArray(node)) return undefined;
    node = node[part];
  }
  return typeof node === "string" ? node : undefined;
}

function readStored(): Lang {
  try {
    return localStorage.getItem(STORAGE_KEY) === "hi" ? "hi" : "en"; // English is the default
  } catch {
    return "en";
  }
}

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  /** Translate a key; `{name}` placeholders are filled from `vars`. Falls back to English, then to the key. */
  t: (key: TKey, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStored);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try { localStorage.setItem(STORAGE_KEY, l); } catch { /* private mode: the choice just isn't remembered */ }
  }, []);

  const t = useCallback<I18nValue["t"]>((key, vars) => {
    let s = lookup(DICTS[lang], key) ?? lookup(DICTS.en, key) ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
    return s;
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <I18nProvider>");
  return ctx;
}

export const useT = () => useI18n().t;
