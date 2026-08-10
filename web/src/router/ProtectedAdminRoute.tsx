import { Skeleton } from "@/components/shared/Skeleton";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/useAuth";
import { hasAdminAccess } from "@/lib/auth";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

export function ProtectedAdminRoute({ children }: { children: ReactNode }) {
  const { status, user } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <main className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-5 py-8">
        <Skeleton className="h-10 w-32 rounded-[var(--radius-md)]" />
        <Skeleton className="h-32 w-full rounded-[var(--radius-lg)]" />
      </main>
    );
  }

  if (!user) {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={`/login?return_to=${encodeURIComponent(returnTo)}`} replace />;
  }

  if (!hasAdminAccess(user.role)) {
    return (
      <main className="mx-auto flex w-full max-w-2xl flex-col items-center gap-4 px-5 py-16 text-center">
        <p className="text-3xl font-semibold text-[color:var(--color-fg-subtle)]">403</p>
        <h1 className="text-xl font-semibold">Admin access required</h1>
        <p className="text-sm text-[color:var(--color-fg-muted)]">
          This route is reserved for the single admin session described in the auth spec.
        </p>
        <Button variant="secondary" onClick={() => window.history.back()}>
          Go back
        </Button>
      </main>
    );
  }

  return children;
}
