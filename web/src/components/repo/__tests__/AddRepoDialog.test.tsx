import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { AddRepoDialog } from "../AddRepoDialog";

const STUB_REPO = {
  id: "r-1",
  git_url: "https://github.com/test/repo",
  name: "repo",
  owner: "test",
  branch: "main",
  status: "pending",
  last_commit: null,
  error_msg: null,
  stats: {
    languages: [],
    modules_count: 0,
    functions_count: 0,
    classes_count: 0,
    documents_count: 0,
  },
  visibility: "admin_only",
  sync_schedule: "manual",
  last_synced_at: null,
  next_sync_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const server = setupServer(
  http.post("/api/repos", () => HttpResponse.json(STUB_REPO, { status: 202 })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderDialog() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <AddRepoDialog />
    </QueryClientProvider>,
  );
  // Open the dialog by clicking the trigger
  fireEvent.click(screen.getByRole("button", { name: /add repo/i }));
}

describe("AddRepoDialog — D6 payload shape", () => {
  it("includes name in POST body when provided", async () => {
    let capturedBody: unknown = null;

    server.use(
      http.post("/api/repos", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    renderDialog();

    await waitFor(() => screen.getByLabelText(/git url/i));

    fireEvent.change(screen.getByLabelText(/git url/i), {
      target: { value: "https://github.com/test/repo" },
    });
    fireEvent.change(screen.getByLabelText(/display name/i), {
      target: { value: "My API" },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /add repo/i }).at(-1)!);
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(capturedBody).not.toBeNull();
    const body = capturedBody as Record<string, unknown>;
    expect(body.git_url).toBe("https://github.com/test/repo");
    expect(body.name).toBe("My API");
    expect(body.visibility).toBe("admin_only");
  });

  it("omits name from POST body when left empty", async () => {
    let capturedBody: unknown = null;

    server.use(
      http.post("/api/repos", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    renderDialog();

    await waitFor(() => screen.getByLabelText(/git url/i));

    fireEvent.change(screen.getByLabelText(/git url/i), {
      target: { value: "https://github.com/test/repo" },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /add repo/i }).at(-1)!);
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(capturedBody).not.toBeNull();
    expect((capturedBody as Record<string, unknown>).name).toBeUndefined();
    expect((capturedBody as Record<string, unknown>).visibility).toBe("admin_only");
  });

  it("includes explicit public visibility in the POST body", async () => {
    let capturedBody: unknown = null;

    server.use(
      http.post("/api/repos", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    renderDialog();

    await waitFor(() => screen.getByLabelText(/git url/i));

    fireEvent.change(screen.getByLabelText(/git url/i), {
      target: { value: "https://github.com/test/repo" },
    });
    fireEvent.click(screen.getByRole("combobox", { name: /visibility/i }));
    fireEvent.click(screen.getAllByText("Public").at(-1)!);

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /add repo/i }).at(-1)!);
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(capturedBody).not.toBeNull();
    expect((capturedBody as Record<string, unknown>).visibility).toBe("public");
  });
});

describe("AddRepoDialog — branch payload (didEditBranch logic)", () => {
  async function submitWithBranch(branchValue?: string): Promise<Record<string, unknown> | null> {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post("/api/repos", async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(STUB_REPO, { status: 202 });
      }),
    );

    renderDialog();
    await waitFor(() => screen.getByLabelText(/git url/i));

    fireEvent.change(screen.getByLabelText(/git url/i), {
      target: { value: "https://github.com/test/repo" },
    });

    if (branchValue !== undefined) {
      fireEvent.change(screen.getByLabelText(/branch/i), {
        target: { value: branchValue },
      });
    }

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /add repo/i }).at(-1)!);
      await new Promise((r) => setTimeout(r, 200));
    });

    return capturedBody;
  }

  it("submit without editing branch → branch: undefined in payload", async () => {
    const body = await submitWithBranch(undefined);
    expect(body).not.toBeNull();
    expect(body!.branch).toBeUndefined();
  });

  it("type 'main' explicitly → branch: 'main' in payload", async () => {
    const body = await submitWithBranch("main");
    expect(body).not.toBeNull();
    expect(body!.branch).toBe("main");
  });

  it("clear the field (empty string) → branch: undefined in payload", async () => {
    const body = await submitWithBranch("");
    expect(body).not.toBeNull();
    expect(body!.branch).toBeUndefined();
  });

  it("type 'feature/x' → branch: 'feature/x' in payload", async () => {
    const body = await submitWithBranch("feature/x");
    expect(body).not.toBeNull();
    expect(body!.branch).toBe("feature/x");
  });
});

describe("AddRepoDialog — Upload .zip tab", () => {
  function makeZipFile(name = "demo.zip", size = 1024) {
    return new File([new Uint8Array(size)], name, { type: "application/zip" });
  }

  it("renders Upload .zip tab with dropzone and disabled submit", async () => {
    renderDialog();
    await waitFor(() => screen.getByLabelText(/git url/i));

    fireEvent.click(screen.getByRole("tab", { name: /upload \.zip/i }));

    await waitFor(() => screen.getByText(/drop a \.zip here/i));
    const submit = screen.getByRole("button", { name: /upload archive/i });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it("rejects non-.zip filenames client-side without hitting the server", async () => {
    renderDialog();
    await waitFor(() => screen.getByLabelText(/git url/i));
    fireEvent.click(screen.getByRole("tab", { name: /upload \.zip/i }));

    const fileInput = screen.getByLabelText("Archive") as HTMLInputElement;
    const bad = new File(["x"], "not-a-zip.tar", { type: "application/x-tar" });

    await act(async () => {
      Object.defineProperty(fileInput, "files", { value: [bad], configurable: true });
      fireEvent.change(fileInput);
    });

    expect(screen.getByText(/filename must end with \.zip/i)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /upload archive/i });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it("uploads multipart/form-data to /api/repos/upload on submit", async () => {
    let archiveSeen = false;
    let capturedVisibility: string | null = null;
    let capturedIdempotency: string | null = null;
    let capturedContentType: string | null = null;
    let capturedHost: string | null = null;
    let capturedOwner: string | null = null;
    let capturedName: string | null = null;

    server.use(
      http.post("/api/repos/upload", async ({ request }) => {
        capturedIdempotency = request.headers.get("Idempotency-Key");
        capturedContentType = request.headers.get("Content-Type");
        const form = await request.formData();
        archiveSeen = form.has("archive");
        capturedVisibility = String(form.get("visibility"));
        capturedHost = String(form.get("host"));
        capturedOwner = String(form.get("owner"));
        capturedName = String(form.get("name"));
        return HttpResponse.json(
          {
            ...STUB_REPO,
            source: "zip",
            host: "local.zip",
            owner: "demo",
            name: "project",
            git_url: "zip://local.zip/demo/project",
          },
          { status: 202 },
        );
      }),
    );

    renderDialog();
    await waitFor(() => screen.getByLabelText(/git url/i));
    fireEvent.click(screen.getByRole("tab", { name: /upload \.zip/i }));

    fireEvent.change(screen.getByLabelText(/^host$/i), { target: { value: "local.zip" } });
    fireEvent.change(screen.getByLabelText(/^owner$/i), { target: { value: "demo" } });
    fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "project" } });

    const fileInput = screen.getByLabelText("Archive") as HTMLInputElement;
    const file = makeZipFile("project.zip", 2048);
    await act(async () => {
      Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
      fireEvent.change(fileInput);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload archive/i }));
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(archiveSeen).toBe(true);
    expect(capturedContentType ?? "").toMatch(/multipart\/form-data/);
    expect(capturedVisibility).toBe("admin_only");
    expect(capturedIdempotency).toMatch(/^[0-9a-f-]{36}$/);
    expect(capturedHost).toBe("local.zip");
    expect(capturedOwner).toBe("demo");
    expect(capturedName).toBe("project");
  });

  it("surfaces 422 ARCHIVE_INVALID with field-level message", async () => {
    server.use(
      http.post("/api/repos/upload", () =>
        HttpResponse.json(
          {
            error: {
              code: "VALIDATION_FAILED",
              message: "Archive invalid",
              request_id: "r",
              field_errors: [
                { field: "archive", code: "ARCHIVE_INVALID", message: "Bad central directory" },
              ],
            },
          },
          { status: 422 },
        ),
      ),
    );

    renderDialog();
    await waitFor(() => screen.getByLabelText(/git url/i));
    fireEvent.click(screen.getByRole("tab", { name: /upload \.zip/i }));

    fireEvent.change(screen.getByLabelText(/^host$/i), { target: { value: "local.zip" } });
    fireEvent.change(screen.getByLabelText(/^owner$/i), { target: { value: "demo" } });
    fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "project" } });

    const fileInput = screen.getByLabelText("Archive") as HTMLInputElement;
    const file = makeZipFile();
    await act(async () => {
      Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
      fireEvent.change(fileInput);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /upload archive/i }));
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(screen.getByText(/bad central directory/i)).toBeInTheDocument();
  });
});

describe("AddRepoDialog — D4 REPOSITORY_EXISTS error copy", () => {
  it("surfaces error message on 409 REPOSITORY_EXISTS", async () => {
    server.use(
      http.post("/api/repos", () =>
        HttpResponse.json(
          {
            error: {
              code: "REPOSITORY_EXISTS",
              message: "Repository already exists",
              request_id: "r",
            },
          },
          { status: 409 },
        ),
      ),
    );

    renderDialog();
    await waitFor(() => screen.getByLabelText(/git url/i));

    fireEvent.change(screen.getByLabelText(/git url/i), {
      target: { value: "https://github.com/test/repo" },
    });

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /add repo/i }).at(-1)!);
      await new Promise((r) => setTimeout(r, 200));
    });

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert").textContent).toContain("Repository already exists");
  });
});
