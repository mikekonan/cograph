import type { WikiTreeNode } from "@/api/types";
import { DocSidebar } from "@/components/docs/DocSidebar";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

function node(slug: string, title: string, children: WikiTreeNode[] = []): WikiTreeNode {
  return {
    id: `wiki-${slug}`,
    slug,
    title,
    sort_order: 0,
    parent_slug: null,
    source_commit: "abc",
    children,
  };
}

function renderTree(tree: WikiTreeNode[], activeSlug?: string) {
  return render(
    <MemoryRouter>
      <DocSidebar
        repo={{ host: "github.com", owner: "acme", name: "repo" }}
        tree={tree}
        activeSlug={activeSlug}
        section="wiki"
      />
    </MemoryRouter>,
  );
}

describe("DocSidebar — 2-level hierarchical wiki tree", () => {
  it("renders parent groups with children indented one level", () => {
    const tree: WikiTreeNode[] = [
      node("index", "Overview"),
      node("generated-code", "Generated Code Structure", [
        { ...node("routes-gen", "routes_gen.go"), parent_slug: "generated-code" },
        { ...node("components-gen", "components_gen.go"), parent_slug: "generated-code" },
      ]),
    ];
    renderTree(tree);

    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Generated Code Structure")).toBeInTheDocument();
    // Children render under the parent (group is open by default).
    expect(screen.getByText("routes_gen.go")).toBeInTheDocument();
    expect(screen.getByText("components_gen.go")).toBeInTheDocument();
  });

  it("auto-expands the group containing the active slug", () => {
    const tree: WikiTreeNode[] = [
      node("generated-code", "Generated Code Structure", [
        { ...node("routes-gen", "routes_gen.go"), parent_slug: "generated-code" },
      ]),
    ];
    renderTree(tree, "routes-gen");

    // ChevronDown rotated -90deg when collapsed; we check the aria-expanded.
    const collapseBtn = screen.getByRole("button", {
      name: /collapse generated code structure/i,
    });
    expect(collapseBtn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("routes_gen.go")).toBeInTheDocument();
  });

  it("collapses the child list when the chevron is clicked", () => {
    const tree: WikiTreeNode[] = [
      node("generated-code", "Generated Code Structure", [
        { ...node("routes-gen", "routes_gen.go"), parent_slug: "generated-code" },
      ]),
    ];
    renderTree(tree);

    expect(screen.getByText("routes_gen.go")).toBeInTheDocument();
    const collapseBtn = screen.getByRole("button", {
      name: /collapse generated code structure/i,
    });
    fireEvent.click(collapseBtn);
    expect(screen.queryByText("routes_gen.go")).not.toBeInTheDocument();
  });
});

describe("DocSidebar — Q3: index pinned + flattened", () => {
  it("renders index as a leaf with no chevron when it has children", () => {
    const tree: WikiTreeNode[] = [
      {
        ...node("index", "Overview", [
          { ...node("architecture", "Architecture"), parent_slug: "index" },
          { ...node("getting-started", "Getting Started"), parent_slug: "index" },
        ]),
        sort_order: 0,
      },
    ];
    renderTree(tree);

    // Index is a flat leaf — no "Collapse Overview" button.
    expect(screen.queryByRole("button", { name: /collapse overview/i })).not.toBeInTheDocument();
    // Children appear at the top level (flat siblings, not nested).
    expect(screen.getByText("Architecture")).toBeInTheDocument();
    expect(screen.getByText("Getting Started")).toBeInTheDocument();
  });

  it("pins index to the top of the list regardless of sort_order", () => {
    const tree: WikiTreeNode[] = [
      { ...node("z-page", "Z page"), sort_order: 0 },
      { ...node("index", "Overview"), sort_order: 99 },
      { ...node("a-page", "A page"), sort_order: 1 },
    ];
    renderTree(tree);

    const links = screen.getAllByRole("link");
    const titles = links.map((a) => a.textContent);
    // Index is first, even with the highest sort_order; the rest follow
    // in their original tree order.
    expect(titles[0]).toBe("Overview");
  });

  it("renders index's children flat (no extra indentation under index)", () => {
    const tree: WikiTreeNode[] = [
      node("index", "Overview", [
        { ...node("architecture", "Architecture"), parent_slug: "index" },
      ]),
    ];
    renderTree(tree);

    const indexLink = screen.getByRole("link", { name: /overview/i });
    const childLink = screen.getByRole("link", { name: /architecture/i });
    // Padding-left at depth 0 is `8 + 0*12 = 8px` — both rows match.
    expect(indexLink.getAttribute("style") ?? "").toContain("padding-left: 8px");
    expect(childLink.getAttribute("style") ?? "").toContain("padding-left: 8px");
  });
});

describe("DocSidebar — P3: filesystem-mirror `_dir-` groups", () => {
  it("renders `_dir-` group nodes as non-navigable folder labels", () => {
    const tree: WikiTreeNode[] = [
      {
        ...node("_dir-docs/api", "Api", [
          { ...node("docs-api-auth", "auth.md"), parent_slug: "_dir-docs/api" },
        ]),
      },
    ];
    renderTree(tree);

    // The directory group title renders, but it's NOT a NavLink — only the
    // leaf is. (The legacy `_group-` prefix has the same affordance; the
    // new `_dir-` prefix mirrors the repo's filesystem hierarchy.)
    expect(screen.getByText("Api")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^api$/i })).toBeNull();
    expect(screen.getByRole("link", { name: /auth\.md/i })).toBeInTheDocument();
  });

  it("surfaces the full directory path as a `title` tooltip on `_dir-` rows", () => {
    const tree: WikiTreeNode[] = [
      {
        ...node("_dir-docs/api", "Api", [
          { ...node("docs-api-auth", "auth.md"), parent_slug: "_dir-docs/api" },
        ]),
      },
    ];
    renderTree(tree);

    // The folder row carries `title="docs/api"` so hover discloses the
    // full path even when the displayed title is just "Api".
    const folderRow = screen.getByText("Api").closest("div[title]");
    expect(folderRow).not.toBeNull();
    expect(folderRow).toHaveAttribute("title", "docs/api");
  });
});
