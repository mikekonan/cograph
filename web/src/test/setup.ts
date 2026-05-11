import "@testing-library/jest-dom";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// CI runners (especially the shared pgw `staging` pool) are noticeably slower
// than local dev. Raise the testing-library async timeout so `findBy*` /
// `waitFor` don't false-fail on render chains that legitimately need a few
// hundred extra ms (React Query + Router + MSW). 5s is well below the vitest
// per-test timeout and only kicks in on genuinely slow runs.
configure({ asyncUtilTimeout: 5000 });

Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
  value: vi.fn(),
  writable: true,
});

afterEach(() => {
  cleanup();
});
