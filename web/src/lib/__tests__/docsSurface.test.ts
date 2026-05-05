import {
  PRIMARY_NATIVE_DOCS_MIN,
  getNativeDocsActionLabel,
  getNativeDocsSurfaceMode,
} from "@/lib/docsSurface";
import { describe, expect, it } from "vitest";

describe("docsSurface", () => {
  it("classifies none, secondary, and primary native docs corpora deterministically", () => {
    expect(getNativeDocsSurfaceMode(0)).toBe("none");
    expect(getNativeDocsSurfaceMode(1)).toBe("secondary");
    expect(getNativeDocsSurfaceMode(PRIMARY_NATIVE_DOCS_MIN - 1)).toBe("secondary");
    expect(getNativeDocsSurfaceMode(PRIMARY_NATIVE_DOCS_MIN)).toBe("primary");
  });

  it("derives overview CTA labels from corpus shape", () => {
    expect(getNativeDocsActionLabel({ documentsCount: 0, hasReadme: false })).toBeNull();
    expect(getNativeDocsActionLabel({ documentsCount: 1, hasReadme: true })).toBe("Open README");
    expect(getNativeDocsActionLabel({ documentsCount: 1, hasReadme: false })).toBe(
      "Open native doc",
    );
    expect(getNativeDocsActionLabel({ documentsCount: 3, hasReadme: true })).toBe(
      "Open native docs",
    );
    expect(getNativeDocsActionLabel({ documentsCount: 4, hasReadme: true })).toBe("Open Docs");
  });
});
