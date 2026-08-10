import { Skeleton } from "@/components/shared/Skeleton";
import { useAuth } from "@/hooks/useAuth";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

/**
 * ProtectedAuthRoute — gates a route on "any authenticated user".
 * Unauthenticated visitors get redirected to /login with return_to;
 * loading state shows the same skeleton as ProtectedAdminRoute so the
 * shell doesn't flicker between the two.
 */
export function ProtectedAuthRoute({ children }: { children: ReactNode }) {
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

  return children;
}
