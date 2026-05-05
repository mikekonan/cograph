import { normalizeWikiMarkdown, stripWikiCitationFootnotes } from "@/lib/wikiContent";
import { describe, expect, it } from "vitest";

describe("stripWikiCitationFootnotes", () => {
  it("removes footnote refs, definitions, and Sources lines", () => {
    const md = ["Hello[^1].", "", "Sources: [^1]", "", "[^1]: foo/bar.go:L1-L5"].join("\n");
    expect(stripWikiCitationFootnotes(md)).toBe("Hello.");
  });
});

describe("normalizeWikiMarkdown — blockquote-as-code repair", () => {
  it("rewrites a blockquote run followed by Source: path:L… as a fenced block", () => {
    const md = [
      "Some prose.",
      "",
      "> type Foo struct {",
      "> 	Bar int",
      "> }",
      "Source: pkg/foo.go:L10-L15",
      "",
      "Trailing prose.",
    ].join("\n");

    const out = normalizeWikiMarkdown(md);

    expect(out).toContain("```go\ntype Foo struct {\n\tBar int\n}\n```");
    expect(out).toContain("Source: pkg/foo.go:L10-L15");
    expect(out).not.toMatch(/^>\s/m);
  });

  it("guesses language from extension (.py → python, .ts → ts)", () => {
    const py = normalizeWikiMarkdown(
      ["> def hello():", ">     return 1", "Source: app/main.py:L1-L2"].join("\n"),
    );
    expect(py).toContain("```python");

    const ts = normalizeWikiMarkdown(
      ["> export const x = 1;", "Source: src/x.ts:L1-L1"].join("\n"),
    );
    expect(ts).toContain("```ts");
  });

  it("leaves a blockquote alone when no Source: attribution follows", () => {
    const md = ["> A real quote from someone.", "", "Trailing prose."].join("\n");
    expect(normalizeWikiMarkdown(md)).toBe(md);
  });

  it("handles a blank line between blockquote and Source line", () => {
    const md = ["> code()", "", "Source: x.go:L1-L1"].join("\n");
    expect(normalizeWikiMarkdown(md)).toContain("```go\ncode()\n```");
  });
});

describe("normalizeWikiMarkdown — malformed inline backtick collapse", () => {
  it("rewrites ```Identifier``` (3+3) to `Identifier` in prose", () => {
    const md = "The ```MerchantID``` type wraps the merchant identifier.";
    expect(normalizeWikiMarkdown(md)).toBe("The `MerchantID` type wraps the merchant identifier.");
  });

  it("rewrites `Identifier``` (1+3 asymmetric) to `Identifier`", () => {
    const md = "`RedirectFollowedEvent``` represents a tracking event.";
    expect(normalizeWikiMarkdown(md)).toBe("`RedirectFollowedEvent` represents a tracking event.");
  });

  it("rewrites ```Identifier` (3+1 asymmetric) to `Identifier`", () => {
    const md = "```NewRedirectFollowedEvent` normalizes the terminal ID.";
    expect(normalizeWikiMarkdown(md)).toBe(
      "`NewRedirectFollowedEvent` normalizes the terminal ID.",
    );
  });

  it("collapses dotted identifiers like ```pkg.Type```", () => {
    const md = "Use ```infra.StoragePrefix``` consistently.";
    expect(normalizeWikiMarkdown(md)).toBe("Use `infra.StoragePrefix` consistently.");
  });

  it("leaves valid 1+1 inline code alone", () => {
    const md = "Use `MAX_RETRIES` here.";
    expect(normalizeWikiMarkdown(md)).toBe(md);
  });

  it("does not touch a real fenced block with a language tag", () => {
    const md = ["```go", "func Foo() {}", "```"].join("\n");
    expect(normalizeWikiMarkdown(md)).toBe(md);
  });

  it("does not touch backticks inside a real fenced block", () => {
    const md = ["```go", "type T struct {", '\tID string `json:"id"`', "}", "```"].join("\n");
    expect(normalizeWikiMarkdown(md)).toBe(md);
  });
});

describe("normalizeWikiMarkdown — lazy-continuation blockquotes", () => {
  it("treats a non-`>` continuation line as part of the blockquote run", () => {
    const md = [
      "> // Foo represents a thing",
      " continues here without `>` prefix",
      "> // and rejoins next line",
      "Source: pkg/foo.go:L1-L5",
    ].join("\n");

    const out = normalizeWikiMarkdown(md);

    expect(out).toContain("```go");
    expect(out).toContain("Source: pkg/foo.go:L1-L5");
    expect(out).not.toMatch(/^>\s/m);
  });

  it("treats a blank line as ending the blockquote", () => {
    const md = ["> first quote", "", "Real prose paragraph.", "", "Trailing."].join("\n");
    expect(normalizeWikiMarkdown(md)).toBe(md);
  });

  it("handles the real-world Redirect-followed events shape", () => {
    const md = [
      "> `RedirectFollowedEvent```",
      " represents a change-operation-state event for redirect_followed action",
      "> This will be sent to bookkeeping via the event bus",
      "Source: domain/redirect_followed.go:L58-L65",
    ].join("\n");

    const out = normalizeWikiMarkdown(md);

    expect(out).toContain("```go");
    // Asymmetric `Foo``` collapsed to `Foo`; lines stay on their original
    // lines (the input had a hard wrap) but they're now all inside the fence.
    expect(out).toContain("`RedirectFollowedEvent`");
    expect(out).toContain("represents a change-operation-state event for redirect_followed action");
    expect(out).toContain("This will be sent to bookkeeping via the event bus");
    expect(out).toContain("Source: domain/redirect_followed.go:L58-L65");
    expect(out).not.toMatch(/^>\s/m);
  });
});
