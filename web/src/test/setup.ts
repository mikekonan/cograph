import "@testing-library/jest-dom";
import type { TestingLibraryMatchers } from "@testing-library/jest-dom/matchers";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jest-dom 7 types its matchers onto Vitest 4's `Assertion<T>`; Vitest 5 reads
// custom matchers from `Matchers<R, T>`. Drop this once jest-dom does it.
declare module "vitest" {
  interface Matchers<R, T> extends TestingLibraryMatchers<unknown, R> {}
}

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
