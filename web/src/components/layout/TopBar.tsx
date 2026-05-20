import { Button } from "@/components/ui/Button";
import type { User } from "@/contexts/AuthContext";
import { useAuth } from "@/hooks/useAuth";
import { useTheme } from "@/hooks/useTheme";
import { hasAdminAccess } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { ChevronDown, Moon, Sun } from "lucide-react";
import { type ReactNode, forwardRef, useEffect, useId, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";

/**
 * TopBar — brand + nav + theme toggle + (session controls if authenticated).
 *
 * Brand is the full `c○graph` wordmark: mono, lowercase, letter-spacing-tight,
 * with the dashed-orbital mark standing in for the lowercase `o`. The mark is
 * nudged down 0.10em so its center aligns with the x-height midpoint — without
 * that it reads as tilted.
 *
 * Active nav item uses the violet accent-subtle pill + an inset ring so it
 * reads as "selected" from a distance (the old bg-hover style was ambiguous
 * against a hovered-but-unselected peer).
 *
 * Height locked to 52px per DESIGN-TOKENS.md §Layout rails.
 */
export function TopBar() {
  const { status, user, logout } = useAuth();
  const { effective, toggle } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const menuId = useId();
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const menuPanelRef = useRef<HTMLDivElement>(null);
  const firstMenuItemRef = useRef<HTMLElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const currentPath = `${location.pathname}${location.search}${location.hash}`;
  const previousPathRef = useRef(currentPath);
  const isAdmin = status === "authenticated" && hasAdminAccess(user?.role);

  useEffect(() => {
    if (previousPathRef.current === currentPath) return;
    previousPathRef.current = currentPath;
    setMenuOpen(false);
  }, [currentPath]);

  useEffect(() => {
    if (!menuOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (
        target &&
        (menuPanelRef.current?.contains(target) || menuTriggerRef.current?.contains(target))
      ) {
        return;
      }
      setMenuOpen(false);
    };

    const handleFocusIn = (event: FocusEvent) => {
      const target = event.target as Node | null;
      if (
        target &&
        (menuPanelRef.current?.contains(target) || menuTriggerRef.current?.contains(target))
      ) {
        return;
      }
      setMenuOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setMenuOpen(false);
      requestAnimationFrame(() => {
        menuTriggerRef.current?.focus();
      });
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen]);

  return (
    <header
      className={cn(
        "sticky top-0 z-[var(--z-sticky)]",
        "flex h-13 items-center justify-between gap-4 px-5",
        "border-b bg-[color:var(--color-bg)]/85 backdrop-blur",
        "border-[color:var(--color-border-subtle)]",
      )}
      style={{ height: "52px" }}
    >
      <div className="flex items-center gap-6">
        <NavLink
          to="/"
          aria-label="Cograph — home"
          className={cn(
            "inline-flex items-baseline font-medium leading-none",
            "font-mono text-[15px] tracking-[-0.01em]",
            "text-[color:var(--color-fg)]",
          )}
        >
          c
          <CographMark className="mx-[0.02em] inline-block h-[0.87em] w-[0.87em] translate-y-[0.10em]" />
          graph
        </NavLink>

        <nav aria-label="Primary navigation" className="flex items-center gap-0.5">
          <TopBarNavLink to="/">Repos</TopBarNavLink>
          <TopBarNavLink to="/docs">Docs</TopBarNavLink>
          {isAdmin && <TopBarNavLink to="/search">Search</TopBarNavLink>}
        </nav>
      </div>

      <section className="flex items-center gap-2" aria-label="Session controls">
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Switch to ${effective === "dark" ? "light" : "dark"} theme`}
          onClick={toggle}
        >
          {effective === "dark" ? (
            <Sun className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Moon className="h-4 w-4" aria-hidden="true" />
          )}
        </Button>

        {status === "authenticated" && user ? (
          <div className="relative">
            <button
              ref={menuTriggerRef}
              type="button"
              aria-controls={menuOpen ? menuId : undefined}
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              aria-label={`Open ${isAdmin ? "admin" : "account"} menu for ${user.email}`}
              className={cn(
                "inline-flex h-9 items-center gap-2 rounded-[var(--radius-full)] border px-1.5 pr-2",
                "border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)]",
                "text-[color:var(--color-fg)] shadow-sm",
                "transition-colors duration-[var(--motion-quick)]",
                "hover:border-[color:var(--color-border)] hover:bg-[color:var(--color-bg-hover)]",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-bg)]",
                menuOpen &&
                  "border-[color:var(--color-accent)] bg-[color:var(--color-accent-subtle)]",
              )}
              onClick={() => setMenuOpen((open) => !open)}
              onKeyDown={(event) => {
                if (event.key !== "ArrowDown") return;
                event.preventDefault();
                setMenuOpen(true);
                requestAnimationFrame(() => {
                  firstMenuItemRef.current?.focus();
                });
              }}
            >
              <span
                className={cn(
                  "inline-flex h-6 w-6 items-center justify-center rounded-[var(--radius-full)]",
                  "bg-[color:var(--color-accent)] font-mono text-xs font-semibold uppercase",
                  "text-[color:var(--color-accent-fg)]",
                )}
                aria-hidden="true"
              >
                {getUserInitials(user)}
              </span>
              <ChevronDown
                className={cn(
                  "h-4 w-4 text-[color:var(--color-fg-muted)] transition-transform duration-[var(--motion-quick)]",
                  menuOpen && "rotate-180 text-[color:var(--color-fg)]",
                )}
                aria-hidden="true"
              />
            </button>

            {menuOpen && (
              <div
                ref={menuPanelRef}
                id={menuId}
                role="menu"
                aria-label={isAdmin ? "Admin menu" : "Account menu"}
                className={cn(
                  "absolute right-0 top-[calc(100%+0.5rem)] z-[var(--z-dropdown)] w-56 max-w-[calc(100vw-2rem)] overflow-hidden",
                  "rounded-[var(--radius-md)] border border-[color:var(--color-border)]",
                  "bg-[color:var(--color-bg-elevated)] shadow-lg",
                  "animate-[scale-in_var(--motion-base)_var(--ease-smooth)]",
                )}
              >
                <div className="px-3 py-2 text-left" role="presentation">
                  <p className="text-2xs font-semibold uppercase tracking-[var(--tracking-eyebrow)] text-[color:var(--color-fg-muted)]">
                    {isAdmin ? "Admin" : "Signed in"}
                  </p>
                  <p className="mt-1 truncate font-mono text-sm text-[color:var(--color-fg)]">
                    {user.email}
                  </p>
                </div>
                {isAdmin && (
                  <>
                    <div
                      aria-hidden="true"
                      className="mx-1 h-px bg-[color:var(--color-border-subtle)]"
                    />
                    <div className="p-1">
                      <TopBarMenuLink
                        ref={firstMenuItemRef}
                        to="/jobs"
                        end
                        onSelect={() => setMenuOpen(false)}
                      >
                        Jobs
                      </TopBarMenuLink>
                      <TopBarMenuLink to="/admin" onSelect={() => setMenuOpen(false)}>
                        Config
                      </TopBarMenuLink>
                    </div>
                  </>
                )}
                <div
                  aria-hidden="true"
                  className="mx-1 h-px bg-[color:var(--color-border-subtle)]"
                />
                <div className="p-1">
                  <TopBarMenuLink
                    ref={isAdmin ? undefined : firstMenuItemRef}
                    to="/account/tokens"
                    onSelect={() => setMenuOpen(false)}
                  >
                    Personal access tokens
                  </TopBarMenuLink>
                </div>
                <div
                  aria-hidden="true"
                  className="mx-1 h-px bg-[color:var(--color-border-subtle)]"
                />
                <div className="p-1">
                  <TopBarMenuAction
                    onSelect={async () => {
                      setMenuOpen(false);
                      await logout();
                      navigate("/", { replace: true });
                    }}
                  >
                    Logout
                  </TopBarMenuAction>
                </div>
              </div>
            )}
          </div>
        ) : status === "anonymous" ? (
          <Button
            size="sm"
            onClick={() =>
              navigate(
                currentPath === "/login"
                  ? "/login"
                  : `/login?return_to=${encodeURIComponent(currentPath)}`,
              )
            }
          >
            Log in
          </Button>
        ) : null}
      </section>
    </header>
  );
}

function TopBarNavLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        cn(
          "inline-flex h-8 items-center rounded-[var(--radius-sm)] px-3",
          "text-sm font-medium",
          "transition-colors duration-[var(--motion-quick)]",
          isActive
            ? // Selected: violet-tinted pill + inset accent ring.
              // Reads as a committed selection even against hover states on peers.
              "bg-[color:var(--color-accent-subtle)] text-[color:var(--color-fg)] shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-accent)_40%,transparent)]"
            : "text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
        )
      }
    >
      {children}
    </NavLink>
  );
}

const topBarMenuItemClassName = (isActive: boolean) =>
  cn(
    "flex w-full items-center rounded-[var(--radius-sm)] px-3 py-2 text-left text-sm",
    "shadow-[inset_0_0_0_1px_transparent] transition-[background-color,color,box-shadow] duration-[var(--motion-quick)]",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/45",
    "focus-visible:ring-offset-1 focus-visible:ring-offset-[color:var(--color-bg-elevated)]",
    isActive
      ? "bg-[color:var(--color-accent-subtle)] text-[color:var(--color-fg)] shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-accent)_40%,transparent)]"
      : "text-[color:var(--color-fg-muted)] hover:bg-[color:var(--color-bg-muted)] hover:text-[color:var(--color-fg)] hover:shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-border-strong)_55%,transparent)]",
  );

const TopBarMenuLink = forwardRef<
  HTMLElement,
  {
    to: string;
    children: ReactNode;
    end?: boolean;
    onSelect: () => void;
  }
>(({ to, children, end, onSelect }, ref) => (
  <NavLink
    ref={ref as React.Ref<HTMLAnchorElement>}
    to={to}
    end={end}
    role="menuitem"
    onClick={onSelect}
    className={({ isActive }) => topBarMenuItemClassName(isActive)}
  >
    {children}
  </NavLink>
));
TopBarMenuLink.displayName = "TopBarMenuLink";

const TopBarMenuAction = forwardRef<
  HTMLElement,
  {
    children: ReactNode;
    onSelect: () => void | Promise<void>;
  }
>(({ children, onSelect }, ref) => (
  <button
    ref={ref as React.Ref<HTMLButtonElement>}
    type="button"
    role="menuitem"
    className={topBarMenuItemClassName(false)}
    onClick={() => {
      void onSelect();
    }}
  >
    {children}
  </button>
));
TopBarMenuAction.displayName = "TopBarMenuAction";

function getUserInitials(user: User) {
  const source = user.name?.trim() || user.email.split("@")[0] || "admin";
  const initials = source
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");

  return initials || "A";
}

/**
 * CographMark — the brand mark (dashed orbital + three accent nodes).
 * Kept inline so the topbar has zero SVG asset dependency — this is the
 * canonical definition, copied verbatim from /design-system/brand-logo.
 * Uses `currentColor` via --color-accent so it picks up theme automatically.
 */
function CographMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <circle
        cx="16"
        cy="16"
        r="13"
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth="2.2"
        strokeDasharray="20.23 7"
        strokeDashoffset="-3.5"
        strokeLinecap="round"
      />
      <circle cx="16" cy="3" r="3.2" fill="var(--color-accent)" />
      <circle cx="4.74" cy="22.5" r="3.2" fill="var(--color-accent)" />
      <circle cx="27.26" cy="22.5" r="3.2" fill="var(--color-accent)" />
    </svg>
  );
}
