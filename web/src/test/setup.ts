import { Blob as NodeBlob, File as NodeFile } from "node:buffer";

import "@testing-library/jest-dom";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// A jsdom test run contains two incompatible sets of File/Blob/FormData: jsdom's
// own, and the ones inside the undici bundled into Node. jsdom shadows those
// three globals but implements neither `fetch` nor `Response`, so those stay
// Node's — which means every `fetch(url, { body: formData })` hands a jsdom
// object to Node's body serialiser.
//
// Node 22 tolerated the mixture. Node 26's multipart serialiser brand-checks
// each entry with `webidl.is.File`, which a jsdom File fails, so an upload now
// dies inside the parser with a bare `ERR_ASSERTION: The expression evaluated to
// a falsy value` and never reaches its handler. This is not specific to MSW or
// to one test: any code doing a multipart fetch breaks the same way.
//
// Fixing it means putting the whole upload path in one universe, and it has to
// be all three classes — swapping only File and Blob just moves the failure, as
// jsdom's FormData then rejects a Node File as "not of type 'Blob'". File and
// Blob come from `node:buffer`; FormData is exposed by no module, so it is read
// off the one Node object that hands one out.
//
// Gives up `new FormData(formElement)`, which jsdom supports and Node does not.
// Nothing in src/ constructs FormData from an element; if that changes, this is
// what the failure will point at.
const nodeMultipart = new Response(
  '--b\r\nContent-Disposition: form-data; name="a"\r\n\r\n1\r\n--b--\r\n',
  {
    headers: { "content-type": "multipart/form-data; boundary=b" },
  },
);
globalThis.FormData = (await nodeMultipart.formData()).constructor as typeof FormData;
globalThis.File = NodeFile as unknown as typeof File;
globalThis.Blob = NodeBlob as unknown as typeof Blob;

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
