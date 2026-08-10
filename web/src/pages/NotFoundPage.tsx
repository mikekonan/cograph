import { Button } from "@/components/ui/Button";
import { useNavigate } from "react-router";

export default function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <p className="text-3xl font-semibold text-[color:var(--color-fg-subtle)]">404</p>
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="max-w-md text-sm text-[color:var(--color-fg-muted)]">
        The page you requested doesn't exist or was moved.
      </p>
      <Button variant="secondary" onClick={() => navigate("/")}>
        Back to home
      </Button>
    </main>
  );
}
