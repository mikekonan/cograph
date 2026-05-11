import { URL, fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    exclude: [...configDefaults.exclude, "e2e/**"],
    // Shared CI runners routinely take 5–8s through Router + React Query
    // + MSW boot for setup-heavy tests. Bump per-test timeout above the
    // default 5s.
    testTimeout: 15000,
    hookTimeout: 15000,
  },
});
