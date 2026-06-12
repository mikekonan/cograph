import "@testing-library/jest-dom";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Shared CI runners are noticeably slower than local dev. Raise the
// testing-library async timeout so `findBy*` / `waitFor` don't false-fail
// on render chains that legitimately need a few extra seconds (React Query
// + Router + MSW). Aligned with the vitest testTimeout in vitest.config.ts;
// only kicks in on genuinely slow runs.
configure({ asyncUtilTimeout: 10000 });

Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
  value: vi.fn(),
  writable: true,
});

// jsdom ships neither ResizeObserver nor the pointer-capture API; Radix
// popper-positioned content (Select/Tooltip) needs both to mount.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
window.HTMLElement.prototype.hasPointerCapture ??= () => false;
window.HTMLElement.prototype.setPointerCapture ??= () => {};
window.HTMLElement.prototype.releasePointerCapture ??= () => {};

afterEach(() => {
  cleanup();
});
