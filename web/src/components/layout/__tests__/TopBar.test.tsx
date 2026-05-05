import { type AuthConfig, AuthContext, type User } from "@/contexts/AuthContext";
import { type EffectiveTheme, ThemeContext, type ThemeMode } from "@/contexts/ThemeContext";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { TopBar } from "../TopBar";

const themeValue: {
  mode: ThemeMode;
  effective: EffectiveTheme;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
} = {
  mode: "dark",
  effective: "dark",
  setMode: vi.fn(),
  toggle: vi.fn(),
};

const baseConfig: AuthConfig = {
  registration_enabled: false,
  public_read: true,
  providers: [{ kind: "password", slug: null, display_name: null, login_url: null, enabled: true }],
};

const adminUser: User = {
  id: "admin-1",
  email: "admin@example.com",
  name: "Admin",
  role: "admin",
  is_owner: true,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

const memberUser: User = {
  id: "member-1",
  email: "member@example.com",
  name: "Member",
  role: "user",
  is_owner: false,
  is_active: true,
  auth_source: "password",
  last_login_at: null,
  created_at: "2026-02-01T00:00:00Z",
};

function renderTopBar({
  route = "/",
  status = "anonymous",
  user = null,
}: {
  route?: string;
  status?: "loading" | "anonymous" | "authenticated";
  user?: User | null;
}) {
  render(
    <ThemeContext.Provider value={themeValue}>
      <AuthContext.Provider
        value={{
          status,
          user,
          config: baseConfig,
          needsBootstrap: false,
          refreshConfig: async () => {},
          login: async () => {},
          logout: async () => {},
          clear: () => {},
          setUser: () => {},
        }}
      >
        <MemoryRouter initialEntries={[route]}>
          <TopBar />
        </MemoryRouter>
      </AuthContext.Provider>
    </ThemeContext.Provider>,
  );
}

describe("TopBar jobs access", () => {
  it("keeps Jobs out of the public navigation for anonymous users", () => {
    renderTopBar({});

    const primaryNav = screen.getByRole("navigation", {
      name: /primary navigation/i,
    });
    expect(within(primaryNav).getByRole("link", { name: "Repos" })).toBeInTheDocument();
    expect(within(primaryNav).getByRole("link", { name: "Search" })).toBeInTheDocument();
    expect(within(primaryNav).queryByRole("link", { name: "Design" })).not.toBeInTheDocument();
    expect(within(primaryNav).queryByRole("link", { name: "Jobs" })).not.toBeInTheDocument();
    expect(within(primaryNav).queryByRole("link", { name: "Config" })).not.toBeInTheDocument();

    const sessionControls = screen.getByRole("region", {
      name: /session controls/i,
    });
    expect(within(sessionControls).queryByRole("link", { name: "Jobs" })).not.toBeInTheDocument();
    expect(within(sessionControls).queryByRole("link", { name: "Config" })).not.toBeInTheDocument();
    expect(
      within(sessionControls).queryByRole("button", {
        name: /open admin menu/i,
      }),
    ).not.toBeInTheDocument();
    expect(within(sessionControls).getByRole("button", { name: /log in/i })).toBeInTheDocument();
  });

  it("shows an avatar-style admin menu with keyboard-openable actions and distinct active styling", () => {
    renderTopBar({ route: "/jobs", status: "authenticated", user: adminUser });

    const primaryNav = screen.getByRole("navigation", {
      name: /primary navigation/i,
    });
    expect(within(primaryNav).queryByRole("link", { name: "Jobs" })).not.toBeInTheDocument();
    expect(within(primaryNav).queryByRole("link", { name: "Config" })).not.toBeInTheDocument();

    const trigger = screen.getByRole("button", {
      name: /open admin menu for admin@example\.com/i,
    });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu", { name: /admin menu/i })).not.toBeInTheDocument();
    expect(screen.queryByText("admin@example.com")).not.toBeInTheDocument();

    fireEvent.keyDown(trigger, { key: "ArrowDown" });

    const menu = screen.getByRole("menu", { name: /admin menu/i });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const jobsItem = within(menu).getByRole("menuitem", { name: "Jobs" });
    const configItem = within(menu).getByRole("menuitem", { name: "Config" });
    const logoutItem = within(menu).getByRole("menuitem", { name: "Logout" });

    expect(jobsItem).toBeInTheDocument();
    expect(configItem).toBeInTheDocument();
    expect(configItem.getAttribute("href")).toBe("/admin");
    expect(logoutItem).toBeInTheDocument();
    expect(jobsItem.className).toContain("bg-[color:var(--color-accent-subtle)]");
    expect(jobsItem.className).toContain(
      "shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-accent)_40%,transparent)]",
    );
    expect(configItem.className).toContain("hover:bg-[color:var(--color-bg-muted)]");
    expect(configItem.className).toContain(
      "hover:shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-border-strong)_55%,transparent)]",
    );
    expect(configItem.className).toContain("focus-visible:ring-offset-1");
    expect(logoutItem.className).toContain("hover:bg-[color:var(--color-bg-muted)]");
    expect(within(menu).queryByRole("menuitem", { name: "Admin" })).not.toBeInTheDocument();
    expect(within(menu).getByText("admin@example.com")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: /admin menu/i })).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("shows an account menu with logout for non-admin authenticated users", () => {
    renderTopBar({ status: "authenticated", user: memberUser });

    const trigger = screen.getByRole("button", {
      name: /open account menu for member@example\.com/i,
    });
    expect(trigger).toBeInTheDocument();

    fireEvent.click(trigger);

    const menu = screen.getByRole("menu", { name: /account menu/i });
    expect(within(menu).getByText("member@example.com")).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Logout" })).toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "Jobs" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "Config" })).not.toBeInTheDocument();
    expect(within(menu).queryByRole("menuitem", { name: "Users" })).not.toBeInTheDocument();
  });

  it("slightly enlarges the inline brand mark without changing the wordmark link", () => {
    renderTopBar({});

    const homeLink = screen.getByRole("link", { name: /cograph — home/i });
    const mark = homeLink.querySelector("svg");

    expect(mark).not.toBeNull();
    expect(mark?.getAttribute("class")).toContain("h-[0.87em]");
    expect(mark?.getAttribute("class")).toContain("w-[0.87em]");
  });
});
