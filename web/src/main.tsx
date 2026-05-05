import { App } from "@/App";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/globals.css";

async function enableMocks() {
  if (import.meta.env.DEV && import.meta.env.VITE_USE_MOCKS !== "false") {
    const { startMockWorker } = await import("@/mocks/browser");
    await startMockWorker();
  }
}

enableMocks().then(() => {
  const container = document.getElementById("root");
  if (!container) throw new Error("#root not found");
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
