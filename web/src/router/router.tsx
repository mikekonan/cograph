import { subscribeAuthFailure } from "@/api/client";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { useAuth } from "@/hooks/useAuth";
import AccountIdentitiesPage from "@/pages/AccountIdentitiesPage";
import AccountTokensPage from "@/pages/AccountTokensPage";
import AdminPage from "@/pages/AdminPage";
import DesignPage from "@/pages/DesignPage";
import HomePage from "@/pages/HomePage";
import JobsPage from "@/pages/JobsPage";
import LoginPage from "@/pages/LoginPage";
import MdCollectionPage from "@/pages/MdCollectionPage";
import MdCollectionsPage from "@/pages/MdCollectionsPage";
import MdDocumentPage from "@/pages/MdDocumentPage";
import MdJobsPage from "@/pages/MdJobsPage";
import NotFoundPage from "@/pages/NotFoundPage";
import RepoDocsPage from "@/pages/RepoDocsPage";
import RepoGraphPage from "@/pages/RepoGraphPage";
import RepoOverviewPage from "@/pages/RepoOverviewPage";
import RepoWikiPage from "@/pages/RepoWikiPage";
import SearchPage from "@/pages/SearchPage";
import SetupPage from "@/pages/SetupPage";
import { useEffect, useState } from "react";
import { Navigate, Outlet, createBrowserRouter, useLocation, useNavigate } from "react-router";
import { ProtectedAdminRoute } from "./ProtectedAdminRoute";
import { ProtectedAuthRoute } from "./ProtectedAuthRoute";

function RootLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-[color:var(--color-bg)] text-[color:var(--color-fg)]">
      <TopBar />
      <AuthRedirector />
      <ErrorBoundary>
        <Outlet />
      </ErrorBoundary>
    </div>
  );
}

function AuthRedirector() {
  const { clear } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const dispose = subscribeAuthFailure(() => {
      clear();
      if (location.pathname === "/login") return;
      const returnTo = `${location.pathname}${location.search}${location.hash}`;
      navigate(`/login?return_to=${encodeURIComponent(returnTo)}`, { replace: true });
    });
    return dispose;
  }, [clear, location.hash, location.pathname, location.search, navigate]);

  return null;
}

/** `/login` must remain usable even while bootstrap is pending (issue #8). */
export function LoginRoute() {
  const { refreshConfig, status } = useAuth();

  useEffect(() => {
    void refreshConfig();
  }, [refreshConfig]);

  if (status === "loading") return null;
  return <LoginPage />;
}

export function SetupRoute() {
  const { needsBootstrap, refreshConfig, status, user } = useAuth();
  const [configChecked, setConfigChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setConfigChecked(false);
    void refreshConfig().finally(() => {
      if (!cancelled) setConfigChecked(true);
    });
    return () => {
      cancelled = true;
    };
  }, [refreshConfig]);

  if (status === "loading") return null;
  if (user) return <Navigate to="/login" replace />;
  if (!configChecked) return null;
  if (!needsBootstrap) return <Navigate to="/login" replace />;
  return <SetupPage />;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    errorElement: (
      <div className="flex min-h-screen flex-col">
        <TopBar />
        <NotFoundPage />
      </div>
    ),
    children: [
      { index: true, element: <HomePage /> },
      {
        path: "search",
        element: (
          <ProtectedAdminRoute>
            <SearchPage />
          </ProtectedAdminRoute>
        ),
      },
      { path: "design", element: <DesignPage /> },
      { path: "login", element: <LoginRoute /> },
      { path: "setup", element: <SetupRoute /> },
      {
        path: "admin",
        element: (
          <ProtectedAdminRoute>
            <AdminPage />
          </ProtectedAdminRoute>
        ),
      },
      { path: "admin/users", element: <Navigate to="/admin?tab=users" replace /> },
      {
        path: "admin/identity-providers",
        element: <Navigate to="/admin?tab=identity-providers" replace />,
      },
      { path: "admin/scim", element: <Navigate to="/admin?tab=scim" replace /> },
      { path: "admin/git-hosts", element: <Navigate to="/admin?tab=git-hosts" replace /> },
      { path: "admin/llm-runtime", element: <Navigate to="/admin?tab=llm-runtime" replace /> },
      { path: "admin/secrets", element: <Navigate to="/admin?tab=llm-runtime" replace /> },
      {
        path: "account/tokens",
        element: (
          <ProtectedAuthRoute>
            <AccountTokensPage />
          </ProtectedAuthRoute>
        ),
      },
      {
        path: "account/identities",
        element: (
          <ProtectedAuthRoute>
            <AccountIdentitiesPage />
          </ProtectedAuthRoute>
        ),
      },
      {
        path: "account/mcp",
        element: <Navigate to="/account/tokens" replace />,
      },
      {
        path: "jobs",
        element: (
          <ProtectedAdminRoute>
            <JobsPage />
          </ProtectedAdminRoute>
        ),
      },
      { path: "repos/:host/:owner/:name", element: <RepoOverviewPage /> },
      { path: "repos/:host/:owner/:name/wiki", element: <RepoWikiPage /> },
      { path: "repos/:host/:owner/:name/wiki/:slug", element: <RepoWikiPage /> },
      { path: "repos/:host/:owner/:name/docs", element: <RepoDocsPage /> },
      { path: "repos/:host/:owner/:name/docs/:slug", element: <RepoDocsPage /> },
      { path: "repos/:host/:owner/:name/graph", element: <RepoGraphPage /> },
      { path: "docs", element: <MdCollectionsPage /> },
      { path: "docs/jobs", element: <MdJobsPage /> },
      { path: "docs/:id", element: <MdCollectionPage /> },
      { path: "docs/:id/documents/:documentId", element: <MdDocumentPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
