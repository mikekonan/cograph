import { type ReactNode, createContext, useCallback, useEffect, useMemo, useState } from "react";

export type ThemeMode = "light" | "dark" | "system";
export type EffectiveTheme = "light" | "dark";

type ThemeContextValue = {
  /** User-chosen preference, including "system". */
  mode: ThemeMode;
  /** Concrete theme currently rendered (never "system"). */
  effective: EffectiveTheme;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
};

export const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "cograph-theme";

function readStoredMode(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // SSR / disabled storage — fall through
  }
  return "dark"; // PRODUCT.md: dark by default
}

function resolveEffective(mode: ThemeMode): EffectiveTheme {
  if (mode !== "system") return mode;
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(effective: EffectiveTheme): void {
  document.documentElement.setAttribute("data-theme", effective);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode());
  const [effective, setEffective] = useState<EffectiveTheme>(() => resolveEffective(mode));

  // Apply DOM attribute whenever the effective theme changes.
  useEffect(() => {
    applyTheme(effective);
  }, [effective]);

  // Listen to system changes only when mode === "system".
  useEffect(() => {
    if (mode !== "system") {
      setEffective(mode);
      return;
    }
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const update = () => setEffective(mql.matches ? "light" : "dark");
    update();
    mql.addEventListener("change", update);
    return () => mql.removeEventListener("change", update);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore
    }
  }, []);

  const toggle = useCallback(() => {
    setMode(effective === "dark" ? "light" : "dark");
  }, [effective, setMode]);

  const value = useMemo<ThemeContextValue>(
    () => ({ mode, effective, setMode, toggle }),
    [mode, effective, setMode, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
