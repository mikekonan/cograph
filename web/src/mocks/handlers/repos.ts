import type {
  ApiErrorBody,
  OffsetPage,
  RepoStatus,
  RepoVisibility,
  Repository,
  SyncSchedule,
} from "@/api/types";
import { getReadableMockRepoBySlug, listReadableMockRepos } from "@/mocks/repoAccess";
import { mockAuth, mockDb } from "@/mocks/state";
import { maybeFail, netDelay, wantEmpty } from "@/mocks/utils";
import { http, HttpResponse } from "msw";

function paginate<T>(items: T[], page: number, perPage: number): OffsetPage<T> {
  const start = (page - 1) * perPage;
  return {
    items: items.slice(start, start + perPage),
    total: items.length,
    page,
    per_page: perPage,
    total_pages: Math.max(1, Math.ceil(items.length / perPage)),
  };
}

function err(code: string, message: string, extra?: Record<string, unknown>): ApiErrorBody {
  const error: ApiErrorBody["error"] & Record<string, unknown> = {
    code,
    message,
    request_id: `req-${Date.now()}`,
  };
  if (extra) Object.assign(error, extra);
  return { error };
}

function requireAdmin() {
  if (mockAuth.isAdmin) return null;
  return HttpResponse.json(err("UNAUTHENTICATED", "Admin login required"), { status: 401 });
}

function parseGitUrlMock(url: string): { host: string; owner: string; name: string } | null {
  try {
    if (url.startsWith("git@")) {
      const at = url.indexOf("@");
      const colon = url.indexOf(":");
      if (at < 0 || colon < at) return null;
      const host = url.slice(at + 1, colon);
      let path = url.slice(colon + 1);
      if (path.endsWith(".git")) path = path.slice(0, -4);
      const parts = path.split("/").filter(Boolean);
      if (parts.length < 2 || !host) return null;
      return { host, owner: parts[parts.length - 2], name: parts[parts.length - 1] };
    }
    const parsed = new URL(url);
    let path = parsed.pathname.replace(/\/+$/g, "");
    if (path.endsWith(".git")) path = path.slice(0, -4);
    const parts = path.split("/").filter(Boolean);
    if (parts.length < 2 || !parsed.hostname) return null;
    return {
      host: parsed.hostname,
      owner: parts[parts.length - 2],
      name: parts[parts.length - 1],
    };
  } catch {
    return null;
  }
}

export const repoHandlers = [
  http.get("/api/repos", async ({ request }) => {
    await netDelay("list");
    const failure = maybeFail();
    if (failure) return failure;

    const url = new URL(request.url);
    const status = url.searchParams.get("status") as RepoStatus | null;
    const search = url.searchParams.get("search")?.toLowerCase();
    const page = Number(url.searchParams.get("page") ?? "1");
    const perPage = Number(url.searchParams.get("per_page") ?? "20");

    if (wantEmpty()) {
      return HttpResponse.json(paginate<Repository>([], page, perPage));
    }

    let items = listReadableMockRepos();
    if (status) items = items.filter((r) => r.status === status);
    if (search) {
      items = items.filter(
        (r) =>
          r.name.toLowerCase().includes(search) ||
          r.owner.toLowerCase().includes(search) ||
          r.host.toLowerCase().includes(search),
      );
    }
    return HttpResponse.json(paginate(items, page, perPage));
  }),

  // Static routes (`/upload`) must be registered before the dynamic
  // 3-segment slug routes so MSW matches them first.
  http.post("/api/repos/upload", async ({ request }) => {
    await netDelay("mutation");
    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    const form = await request.formData();
    const archive = form.get("archive");
    if (!(archive instanceof File)) {
      return HttpResponse.json(
        {
          error: {
            code: "VALIDATION_FAILED",
            message: "Request validation failed",
            request_id: `req-${Date.now()}`,
            field_errors: [
              { field: "archive", code: "MISSING_FILE", message: "Archive file is required" },
            ],
          },
        },
        { status: 422 },
      );
    }
    if (!archive.name.toLowerCase().endsWith(".zip")) {
      return HttpResponse.json(
        {
          error: {
            code: "VALIDATION_FAILED",
            message: "Filename must end with .zip",
            request_id: `req-${Date.now()}`,
            field_errors: [
              { field: "archive", code: "BAD_FILENAME", message: "Filename must end with .zip" },
            ],
          },
        },
        { status: 422 },
      );
    }
    if (
      archive.type &&
      archive.type !== "application/zip" &&
      archive.type !== "application/x-zip-compressed"
    ) {
      return HttpResponse.json(err("UNSUPPORTED_MEDIA_TYPE", "Only application/zip is accepted"), {
        status: 415,
      });
    }

    const visibility = (form.get("visibility") as RepoVisibility | null) ?? "admin_only";
    const host = String(form.get("host") ?? "").trim();
    const owner = String(form.get("owner") ?? "").trim();
    const name = String(form.get("name") ?? "").trim();
    if (!host || !owner || !name) {
      return HttpResponse.json(
        {
          error: {
            code: "VALIDATION_FAILED",
            message: "host/owner/name are required",
            request_id: `req-${Date.now()}`,
            field_errors: [
              { field: "host", code: "REQUIRED", message: "host is required" },
              { field: "owner", code: "REQUIRED", message: "owner is required" },
              { field: "name", code: "REQUIRED", message: "name is required" },
            ],
          },
        },
        { status: 422 },
      );
    }

    if (mockDb.repos.some((r) => r.host === host && r.owner === owner && r.name === name)) {
      return HttpResponse.json(
        err("REPOSITORY_EXISTS", "Repository already exists", {
          host,
          owner,
          name,
          existing_url: `/repos/${host}/${owner}/${name}`,
        }),
        { status: 409 },
      );
    }

    const id = crypto.randomUUID();
    const now = new Date().toISOString();
    const repo: Repository = {
      id,
      git_url: `zip://${host}/${owner}/${name}`,
      source: "zip",
      host,
      name,
      owner,
      branch: "upload",
      status: "pending",
      last_commit: Math.random().toString(16).slice(2).padEnd(64, "0").slice(0, 64),
      error_msg: null,
      stats: {
        languages: [],
        modules_count: 0,
        functions_count: 0,
        classes_count: 0,
        documents_count: 0,
      },
      visibility,
      sync_schedule: "manual",
      last_synced_at: null,
      next_sync_at: null,
      created_at: now,
      updated_at: now,
    };
    mockDb.repos.unshift(repo);
    advanceStatus(repo.id);
    return HttpResponse.json(repo, { status: 202 });
  }),

  http.post("/api/repos", async ({ request }) => {
    await netDelay("mutation");
    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    const body = (await request.json()) as {
      git_url?: string;
      branch?: string;
      visibility?: RepoVisibility;
    };

    if (!body.git_url || !/^https?:\/\/|^git@/.test(body.git_url)) {
      return HttpResponse.json(
        {
          error: {
            code: "VALIDATION_FAILED",
            message: "Request validation failed",
            request_id: `req-${Date.now()}`,
            field_errors: [
              { field: "git_url", code: "INVALID_URL", message: "Must be a valid git URL" },
            ],
          },
        },
        { status: 422 },
      );
    }

    const parsed = parseGitUrlMock(body.git_url);
    if (!parsed) {
      return HttpResponse.json(
        {
          error: {
            code: "VALIDATION_FAILED",
            message: "Could not parse host/owner/name from git URL",
            request_id: `req-${Date.now()}`,
            field_errors: [
              { field: "git_url", code: "INVALID_URL", message: "Must be a valid git URL" },
            ],
          },
        },
        { status: 422 },
      );
    }

    if (
      mockDb.repos.some(
        (r) => r.host === parsed.host && r.owner === parsed.owner && r.name === parsed.name,
      )
    ) {
      return HttpResponse.json(
        err("REPOSITORY_EXISTS", "Repository already exists", {
          host: parsed.host,
          owner: parsed.owner,
          name: parsed.name,
          existing_url: `/repos/${parsed.host}/${parsed.owner}/${parsed.name}`,
        }),
        { status: 409 },
      );
    }

    const now = new Date().toISOString();
    const repo: Repository = {
      id: crypto.randomUUID(),
      git_url: body.git_url,
      source: "git",
      host: parsed.host,
      name: parsed.name,
      owner: parsed.owner,
      branch: body.branch ?? "main",
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
      visibility: body.visibility ?? "admin_only",
      sync_schedule: "manual",
      last_synced_at: null,
      next_sync_at: null,
      created_at: now,
      updated_at: now,
    };
    mockDb.repos.unshift(repo);

    // Kick off a fake background pipeline: pending → cloning → indexing →
    // embedding → generating → ready
    advanceStatus(repo.id);

    return HttpResponse.json(repo, { status: 202 });
  }),

  http.get("/api/repos/:host/:owner/:name", async ({ params }) => {
    await netDelay("detail");
    const failure = maybeFail();
    if (failure) return failure;

    const repo = getReadableMockRepoBySlug(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }
    return HttpResponse.json(repo);
  }),

  http.patch("/api/repos/:host/:owner/:name", async ({ params, request }) => {
    await netDelay("mutation");
    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    const body = (await request.json()) as {
      sync_schedule?: SyncSchedule;
      visibility?: RepoVisibility;
    };

    const repo = getReadableMockRepoBySlug(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }

    const validSchedules: SyncSchedule[] = ["manual", "hourly", "daily", "weekly", "webhook"];
    const validVisibilities: RepoVisibility[] = ["public", "admin_only"];
    if (body.sync_schedule && !validSchedules.includes(body.sync_schedule)) {
      return HttpResponse.json(
        {
          error: {
            code: "VALIDATION_FAILED",
            message: "Request validation failed",
            request_id: `req-${Date.now()}`,
            field_errors: [
              {
                field: "sync_schedule",
                code: "INVALID_VALUE",
                message: `Must be one of ${validSchedules.join(", ")}`,
              },
            ],
          },
        },
        { status: 422 },
      );
    }

    if (body.sync_schedule) {
      repo.sync_schedule = body.sync_schedule;
      repo.next_sync_at = computeMockNextSyncAt(body.sync_schedule);
    }
    if (body.visibility) {
      if (!validVisibilities.includes(body.visibility)) {
        return HttpResponse.json(
          {
            error: {
              code: "VALIDATION_FAILED",
              message: "Request validation failed",
              request_id: `req-${Date.now()}`,
              field_errors: [
                {
                  field: "visibility",
                  code: "INVALID_VALUE",
                  message: `Must be one of ${validVisibilities.join(", ")}`,
                },
              ],
            },
          },
          { status: 422 },
        );
      }
      repo.visibility = body.visibility;
    }
    repo.updated_at = new Date().toISOString();
    return HttpResponse.json(repo);
  }),

  http.delete("/api/repos/:host/:owner/:name", async ({ params }) => {
    await netDelay("mutation");
    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    const idx = mockDb.repos.findIndex(
      (r) =>
        r.host === String(params.host) &&
        r.owner === String(params.owner) &&
        r.name === String(params.name),
    );
    if (idx === -1) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }
    mockDb.repos.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/repos/:host/:owner/:name/reindex", async ({ params }) => {
    await netDelay("mutation");
    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    const repo = getReadableMockRepoBySlug(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }
    if (repo.source === "zip") {
      return HttpResponse.json(
        err("REINDEX_DISABLED_FOR_ZIP", "Re-index is disabled for uploaded archives"),
        { status: 409 },
      );
    }

    repo.status = "pending";
    repo.updated_at = new Date().toISOString();
    advanceStatus(repo.id);
    return HttpResponse.json({ id: repo.id, status: "pending" });
  }),

  http.post("/api/repos/:host/:owner/:name/upload", async ({ params, request }) => {
    await netDelay("mutation");
    const authError = requireAdmin();
    if (authError) return authError;

    const failure = maybeFail();
    if (failure) return failure;

    const repo = getReadableMockRepoBySlug(
      String(params.host),
      String(params.owner),
      String(params.name),
    );
    if (!repo) {
      return HttpResponse.json(err("NOT_FOUND", "Repository not found"), { status: 404 });
    }
    if (repo.source !== "zip") {
      return HttpResponse.json(
        err("NOT_ZIP_SOURCED", "Only zip-sourced repositories accept archive uploads"),
        { status: 409 },
      );
    }

    const form = await request.formData();
    const archive = form.get("archive");
    if (!(archive instanceof File) || !archive.name.toLowerCase().endsWith(".zip")) {
      return HttpResponse.json(err("VALIDATION_FAILED", "Archive .zip file required"), {
        status: 422,
      });
    }

    repo.status = "pending";
    repo.last_commit = Math.random().toString(16).slice(2).padEnd(64, "0").slice(0, 64);
    repo.updated_at = new Date().toISOString();
    advanceStatus(repo.id);
    return HttpResponse.json(
      {
        repository_id: repo.id,
        sync_run_id: crypto.randomUUID(),
        status: "pending",
        deduplicated: false,
      },
      { status: 202 },
    );
  }),
];

function computeMockNextSyncAt(schedule: SyncSchedule): string | null {
  const now = new Date();

  switch (schedule) {
    case "manual":
    case "webhook":
      return null;
    case "hourly": {
      const next = new Date(now);
      next.setUTCMinutes(0, 0, 0);
      next.setUTCHours(next.getUTCHours() + 1);
      return next.toISOString();
    }
    case "daily": {
      const next = new Date(now);
      next.setUTCHours(2, 0, 0, 0);
      if (next <= now) {
        next.setUTCDate(next.getUTCDate() + 1);
      }
      return next.toISOString();
    }
    case "weekly": {
      const next = new Date(now);
      next.setUTCHours(2, 0, 0, 0);
      const dayOfWeek = next.getUTCDay() || 7;
      const daysUntilMonday = (8 - dayOfWeek) % 7;
      next.setUTCDate(next.getUTCDate() + daysUntilMonday);
      if (next <= now) {
        next.setUTCDate(next.getUTCDate() + 7);
      }
      return next.toISOString();
    }
  }
}

/**
 * Background status progression. When a new repo is POSTed, we walk it through
 * the lifecycle on a timer so the HomePage can show live status changes on
 * refetch. Not part of the real backend contract — pure MSW theatre.
 */
function advanceStatus(repoId: string) {
  const sequence: Array<{ status: RepoStatus; delayMs: number }> = [
    { status: "cloning", delayMs: 1_500 },
    { status: "indexing", delayMs: 3_000 },
    { status: "embedding", delayMs: 3_500 },
    { status: "generating", delayMs: 2_500 },
    { status: "ready", delayMs: 2_500 },
  ];

  let elapsed = 0;
  for (const step of sequence) {
    elapsed += step.delayMs;
    setTimeout(() => {
      const repo = mockDb.repos.find((r) => r.id === repoId);
      if (!repo) return;
      repo.status = step.status;
      repo.updated_at = new Date().toISOString();
      if (step.status === "ready") {
        repo.last_commit = Math.random().toString(16).slice(2, 9);
        repo.last_synced_at = new Date().toISOString();
        repo.stats = {
          languages: ["typescript"],
          language_bytes: {
            typescript: 400_000 + Math.floor(Math.random() * 400_000),
            javascript: 40_000 + Math.floor(Math.random() * 80_000),
            css: 10_000 + Math.floor(Math.random() * 30_000),
          },
          modules_count: Math.floor(10 + Math.random() * 40),
          functions_count: Math.floor(100 + Math.random() * 500),
          classes_count: Math.floor(10 + Math.random() * 80),
          documents_count: Math.floor(5 + Math.random() * 20),
          total_nodes: Math.floor(200 + Math.random() * 800),
        };
      }
    }, elapsed);
  }
}
