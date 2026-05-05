import { ThemeProvider } from "@/contexts/ThemeContext";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  MarkdownRenderer,
  SafeMarkdownRenderer,
  transformUnresolvedMarkers,
} from "../MarkdownRenderer";

describe("transformUnresolvedMarkers", () => {
  it("returns the input verbatim when no markers are present", () => {
    const md = "# Page\n\nNothing to do here.";
    expect(transformUnresolvedMarkers(md)).toBe(md);
  });

  it("rewrites markers in prose into a styled span with the key", () => {
    const md = "The user calls ⚠️ unresolved: node:pkg.MissingFn at startup.";
    const out = transformUnresolvedMarkers(md);
    expect(out).toContain('<span class="cograph-unresolved"');
    expect(out).toContain('data-key="node:pkg.MissingFn"');
    expect(out).toContain("⚠ unresolved: node:pkg.MissingFn");
    expect(out).not.toContain("⚠️ unresolved: node:pkg.MissingFn");
  });

  it("does not rewrite markers inside fenced code blocks", () => {
    const md = [
      "```python",
      "# ⚠️ unresolved: node:literal.in.code",
      "```",
      "",
      "Outside ⚠️ unresolved: node:fix.me",
    ].join("\n");
    const out = transformUnresolvedMarkers(md);
    expect(out).toContain("# ⚠️ unresolved: node:literal.in.code");
    expect(out).toContain('data-key="node:fix.me"');
  });

  it("does not rewrite markers inside inline backtick code", () => {
    const md = "Inline `⚠️ unresolved: node:keep` should stay literal";
    const out = transformUnresolvedMarkers(md);
    expect(out).toContain("`⚠️ unresolved: node:keep`");
    expect(out).not.toContain('class="cograph-unresolved"');
  });

  it("escapes HTML-unsafe characters in the captured key", () => {
    const md = "See ⚠️ unresolved: doc:<script>.md elsewhere";
    const out = transformUnresolvedMarkers(md);
    expect(out).toContain('data-key="doc:&lt;script&gt;.md"');
    expect(out).not.toContain("<script>");
  });
});

describe("MarkdownRenderer", () => {
  it("keeps raw HTML for trusted generated/repo markdown", () => {
    render(<MarkdownRenderer source={'<span data-testid="trusted-html">ok</span>'} />);

    expect(screen.getByTestId("trusted-html")).toHaveTextContent("ok");
  });

  it("promotes multi-line single-backtick spans to a code block", () => {
    // Regression: writer occasionally wraps a whole Go function in single
    // backticks. Renderer must surface that as a `<figure>`-wrapped
    // CodeBlock, not an inline pill with the delimiters leaking through.
    const md =
      "Bootstrap entry.\n\n" +
      "`func Initialize(ctx context.Context) (err error) {\n" +
      '  if state.Context != nil { return errors.New("already initialized") }\n' +
      "  return\n" +
      "}`\n";
    const { container } = render(
      <ThemeProvider>
        <MarkdownRenderer source={md} />
      </ThemeProvider>,
    );
    // CodeBlock is a `<figure>` (Copy button + content). Inline `<code>`
    // doesn't produce one. Asserting the figure proves we promoted to
    // block render via the function-body heuristic (`{` + `}` + len >= 40).
    expect(container.querySelector("figure")).not.toBeNull();
  });

  it("disables raw HTML and unsafe URLs for uploaded markdown previews", () => {
    render(
      <SafeMarkdownRenderer
        source={
          '<img src=x onerror="alert(1)" />\n\n[bad](javascript:alert(1))\n\n[good](https://example.com)'
        }
      />,
    );

    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("bad").closest("a")?.getAttribute("href") ?? "").not.toMatch(
      /^javascript:/i,
    );
    expect(screen.getByRole("link", { name: "good" })).toHaveAttribute(
      "href",
      "https://example.com",
    );
  });

  describe("Q1.3: render-time qn= injection on graph citation links", () => {
    it("appends ?qn=<qn> to citation graph links with backticked QN labels", () => {
      const md =
        "[`pkg.Type.method`](/repos/example.com/acme/widget/graph?node=11111111-2222-3333-4444-555555555555).";
      render(<MarkdownRenderer source={md} />);
      const link = screen.getByRole("link", { name: /pkg\.type\.method/i });
      const href = link.getAttribute("href") ?? "";
      expect(href).toContain(
        "/repos/example.com/acme/widget/graph?node=11111111-2222-3333-4444-555555555555",
      );
      expect(href).toContain("&qn=pkg.Type.method");
    });

    it("does not modify links to other paths", () => {
      const md = "[other](/repos/example.com/acme/widget/docs/architecture).";
      render(<MarkdownRenderer source={md} />);
      const link = screen.getByRole("link", { name: /other/i });
      expect(link.getAttribute("href")).toBe("/repos/example.com/acme/widget/docs/architecture");
    });

    it("skips injection when the link label is not pure backticked text", () => {
      const md =
        "[free-form label](/repos/example.com/acme/widget/graph?node=11111111-2222-3333-4444-555555555555)";
      render(<MarkdownRenderer source={md} />);
      const link = screen.getByRole("link", { name: /free-form label/i });
      expect(link.getAttribute("href") ?? "").not.toContain("qn=");
    });

    it("is idempotent — already-suffixed hrefs are not re-suffixed", () => {
      const md =
        "[`pkg.X`](/repos/example.com/acme/widget/graph?node=11111111-2222-3333-4444-555555555555&qn=pkg.X)";
      render(<MarkdownRenderer source={md} />);
      const link = screen.getByRole("link", { name: /pkg\.x/i });
      const href = link.getAttribute("href") ?? "";
      // Exactly one `qn=` segment; we did not append a second one.
      expect((href.match(/qn=/g) ?? []).length).toBe(1);
    });
  });
});
