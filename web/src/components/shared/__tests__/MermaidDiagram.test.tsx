import { describe, expect, it } from "vitest";

import { sanitizeMermaidSvg } from "../MermaidDiagram";

describe("sanitizeMermaidSvg", () => {
  it("preserves a vanilla mermaid-style SVG with HTML labels", () => {
    // Smoke test: the path we actually render must survive the
    // sanitize pass — <foreignObject> wrapping a <span> with the line-
    // break <br> that the htmlLabels mode emits for long FQN labels.
    const svg = [
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">',
      '<g class="node">',
      '<rect x="0" y="0" width="200" height="80" />',
      '<foreignObject width="200" height="80">',
      '<span class="nodeLabel">pkg.Type<br/>method</span>',
      "</foreignObject>",
      "</g>",
      "</svg>",
    ].join("");
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned).toContain("<svg");
    expect(cleaned).toContain("foreignObject");
    expect(cleaned.toLowerCase()).toContain("<span");
    expect(cleaned.toLowerCase()).toContain("<br");
    expect(cleaned).toContain("pkg.Type");
  });

  it("strips inline event handlers from SVG attributes", () => {
    // HIGH-07 regression guard: a crafted Mermaid label could embed
    // SVG attribute event handlers that survive securityLevel:antiscript.
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg"><g onclick="window.__bad = true"><rect onload="alert(1)" width="10" height="10"/></g></svg>';
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned.toLowerCase()).not.toContain("onclick");
    expect(cleaned.toLowerCase()).not.toContain("onload");
  });

  it("strips inline event handlers from foreignObject HTML labels", () => {
    // HIGH-07 regression guard: <img onerror=...> inside an HTML label
    // is the canonical Mermaid XSS payload from the finding writeup.
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><img src="x" onerror="window.__bad = true"/></foreignObject></svg>';
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned.toLowerCase()).not.toContain("onerror");
  });

  it("removes <script> tags entirely", () => {
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg"><script>window.__pwn = true;</script><rect width="10" height="10"/></svg>';
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned.toLowerCase()).not.toContain("<script");
  });

  it("removes javascript: URLs from href / xlink:href", () => {
    // Defense in depth: <a xlink:href="javascript:alert(1)"> would fire on click.
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><a href="javascript:alert(1)" xlink:href="javascript:alert(2)"><rect width="10" height="10"/></a></svg>';
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned.toLowerCase()).not.toContain("javascript:");
  });

  it("preserves benign href values (https)", () => {
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg"><a href="https://example.com/docs"><rect width="10" height="10"/></a></svg>';
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned).toContain("https://example.com/docs");
  });

  it("kills the canonical Mermaid HTML-label XSS payload from the finding", () => {
    // From SECURITY_FINDINGS HIGH-07:
    //   A["<img src=x onerror=alert(document.cookie)>"]
    // After Mermaid renders to SVG with htmlLabels, the rendered output
    // contains the <img onerror=...> inside foreignObject. We need that
    // attribute gone.
    const svg = [
      '<svg xmlns="http://www.w3.org/2000/svg">',
      '<foreignObject><div class="nodeLabel">',
      '<img src="x" onerror="alert(document.cookie)" />',
      "</div></foreignObject>",
      "</svg>",
    ].join("");
    const cleaned = sanitizeMermaidSvg(svg);
    expect(cleaned.toLowerCase()).not.toContain("onerror");
  });
});
