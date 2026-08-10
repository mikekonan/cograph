import { handlers } from "@/mocks/handlers";
import { setupWorker } from "msw/browser";

export const worker = setupWorker(...handlers);

/**
 * Start the MSW worker. Idempotent — safe to call on HMR reloads.
 * Bypasses unmatched requests so Vite's own HMR + static assets still work.
 */
export async function startMockWorker() {
  return worker.start({
    onUnhandledRequest: "bypass",
    serviceWorker: {
      url: "/mockServiceWorker.js",
    },
  });
}
