import type { WikiCitation } from "@/api/types";
import { WikiCitationSources } from "@/components/docs/WikiCitationSources";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

function citation(idx: number, kind: "node" | "repo_doc_chunk" = "node"): WikiCitation {
  return {
    id: `cite-${idx}`,
    kind,
    label: `symbol_${idx}`,
    file_path: `src/file_${idx}.py`,
    start_line: 1,
    end_line: 5,
    heading_path: [],
  };
}

const repo = { host: "github.com", owner: "acme", name: "repo" };

describe("WikiCitationSources — Q4: cap + disclosure", () => {
  it("renders all citations directly when count ≤ 10 (no toggle)", () => {
    const citations = Array.from({ length: 8 }, (_, i) => citation(i));
    render(
      <MemoryRouter>
        <WikiCitationSources citations={citations} repo={repo} />
      </MemoryRouter>,
    );
    // All 8 visible.
    for (let i = 0; i < 8; i++) {
      expect(screen.getByText(`symbol_${i}`)).toBeInTheDocument();
    }
    // No "+N more sources" toggle.
    expect(screen.queryByRole("button", { name: /more sources/i })).not.toBeInTheDocument();
  });

  it("caps at 10 with a +N more sources toggle when count > 10", () => {
    const citations = Array.from({ length: 14 }, (_, i) => citation(i));
    render(
      <MemoryRouter>
        <WikiCitationSources citations={citations} repo={repo} />
      </MemoryRouter>,
    );
    // First 10 visible.
    for (let i = 0; i < 10; i++) {
      expect(screen.getByText(`symbol_${i}`)).toBeInTheDocument();
    }
    // 11th and beyond are hidden.
    expect(screen.queryByText("symbol_10")).not.toBeInTheDocument();
    // Toggle exists with the right label.
    const toggle = screen.getByRole("button", { name: /\+4 more sources/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("expands to reveal all citations when toggle clicked, collapses on second click", () => {
    const citations = Array.from({ length: 14 }, (_, i) => citation(i));
    render(
      <MemoryRouter>
        <WikiCitationSources citations={citations} repo={repo} />
      </MemoryRouter>,
    );
    const toggle = screen.getByRole("button", { name: /\+4 more sources/i });
    fireEvent.click(toggle);

    // All 14 now visible.
    for (let i = 0; i < 14; i++) {
      expect(screen.getByText(`symbol_${i}`)).toBeInTheDocument();
    }
    // Toggle now reads "Show fewer sources" with aria-expanded=true.
    const collapse = screen.getByRole("button", { name: /show fewer sources/i });
    expect(collapse).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(collapse);
    // Back to 10.
    expect(screen.queryByText("symbol_10")).not.toBeInTheDocument();
    expect(screen.getByText("symbol_9")).toBeInTheDocument();
  });

  it("singularises +1 more source", () => {
    const citations = Array.from({ length: 11 }, (_, i) => citation(i));
    render(
      <MemoryRouter>
        <WikiCitationSources citations={citations} repo={repo} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: /^\+1 more source$/i })).toBeInTheDocument();
  });

  it("appends ?qn=<label> to kind=node citation hrefs for the by-qn fallback (Q1.3)", () => {
    const nodeCite: WikiCitation = {
      id: "11111111-1111-4111-9111-111111111111",
      kind: "node",
      label: "domain.MerchantID",
      file_path: "domain/merchant.go",
      start_line: 10,
      end_line: 20,
      heading_path: [],
    };
    render(
      <MemoryRouter>
        <WikiCitationSources citations={[nodeCite]} repo={repo} />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link");
    const href = link.getAttribute("href") ?? "";
    expect(href).toContain(`node=${encodeURIComponent(nodeCite.id)}`);
    expect(href).toContain(`qn=${encodeURIComponent(nodeCite.label)}`);
  });

  it("renders nothing when citations is empty", () => {
    const { container } = render(
      <MemoryRouter>
        <WikiCitationSources citations={[]} repo={repo} />
      </MemoryRouter>,
    );
    expect(container.firstChild).toBeNull();
  });
});
