import { ThemeContext } from "@/contexts/ThemeContext";
import { useContext } from "react";

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within <ThemeProvider>");
  return ctx;
}
