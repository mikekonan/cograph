import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { RepoTabHeader } from "../RepoTabs";

const REPO = { host: "github.com", owner: "acme", name: "repo" } as const;

describe("RepoTabHeader", () => {
  it("keeps the repo header compact without subtitle narration or verbose tooltips", () => {
    render(
      <MemoryRouter initialEntries={["/repos/github.com/acme/repo/docs"]}>
        <RepoTabHeader repo={REPO} documentsCount={1} />
      </MemoryRouter>,
    );

    const tabs = screen.getByRole("navigation", { name: /repository sections/i });

    expect(tabs.parentElement).toHaveClass("flex", "flex-col");
    expect(
      screen.queryByText(/generated repo guide with citations back to graph and source context/i),
    ).toBeNull();
    expect(
      screen.queryByText(/README and markdown files that already live inside the repository/i),
    ).toBeNull();
    expect(within(tabs).getByRole("link", { name: "Wiki" })).not.toHaveAttribute("title");
    expect(within(tabs).getByRole("link", { name: "Docs" })).not.toHaveAttribute("title");
  });

  it("hides the docs tab on non-doc routes when the native docs corpus is sparse", () => {
    render(
      <MemoryRouter initialEntries={["/repos/github.com/acme/repo"]}>
        <RepoTabHeader repo={REPO} documentsCount={3} />
      </MemoryRouter>,
    );

    const tabs = screen.getByRole("navigation", { name: /repository sections/i });
    expect(within(tabs).queryByRole("link", { name: "Docs" })).toBeNull();
  });

  it("keeps the docs tab in the primary nav for richer native docs corpora", () => {
    render(
      <MemoryRouter initialEntries={["/repos/github.com/acme/repo"]}>
        <RepoTabHeader repo={REPO} documentsCount={4} />
      </MemoryRouter>,
    );

    const tabs = screen.getByRole("navigation", { name: /repository sections/i });
    expect(within(tabs).getByRole("link", { name: "Docs" })).toBeInTheDocument();
  });
});
