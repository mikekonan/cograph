import { apiFetch, apiJson } from "@/api/client";
import type { OffsetPage, RepoSlug, Repository, SubmitRepoRequest } from "@/api/types";
import { repoApiPath } from "@/lib/repoPath";
import {
  applyFirstRunPlaceholder,
  getFirstRunLifecycleStatus,
  getFirstRunLifecycleTotalMs,
  isInFlightRepoStatus,
} from "@/lib/repoStatus";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

const CREATED_REPO_LIFECYCLES_QUERY_KEY = ["created-repo-lifecycles"] as const;

type CreatedRepoLifecycleMap = Record<string, number>;

function getCreatedRepoLifecycles(qc: ReturnType<typeof useQueryClient>): CreatedRepoLifecycleMap {
  return qc.getQueryData<CreatedRepoLifecycleMap>(CREATED_REPO_LIFECYCLES_QUERY_KEY) ?? {};
}

function setCreatedRepoLifecycle(
  qc: ReturnType<typeof useQueryClient>,
  repoId: string,
  startedAtMs: number = Date.now(),
) {
  qc.setQueryData<CreatedRepoLifecycleMap>(CREATED_REPO_LIFECYCLES_QUERY_KEY, (prev = {}) => ({
    ...prev,
    [repoId]: startedAtMs,
  }));
}

function hasActiveCreatedRepoLifecycle(
  qc: ReturnType<typeof useQueryClient>,
  repoId?: string,
): boolean {
  const lifecycles = getCreatedRepoLifecycles(qc);
  const now = Date.now();
  const totalMs = getFirstRunLifecycleTotalMs();

  if (repoId) {
    const startedAtMs = lifecycles[repoId];
    return typeof startedAtMs === "number" && now - startedAtMs < totalMs;
  }

  return Object.values(lifecycles).some((startedAtMs) => now - startedAtMs < totalMs);
}

function applyCreatedRepoLifecycle(
  qc: ReturnType<typeof useQueryClient>,
  repo: Repository,
): Repository {
  if (repo.status !== "ready") return repo;

  const startedAtMs = getCreatedRepoLifecycles(qc)[repo.id];
  if (typeof startedAtMs !== "number") return repo;

  const syntheticStatus = getFirstRunLifecycleStatus(startedAtMs, Date.now());
  if (syntheticStatus === null) return repo;

  return applyFirstRunPlaceholder(repo, syntheticStatus);
}

function seedCreatedRepoIntoListCaches(qc: ReturnType<typeof useQueryClient>, repo: Repository) {
  const placeholder = applyFirstRunPlaceholder(repo, "pending");
  const repoSearch = `${repo.owner}/${repo.name}`.toLowerCase();

  for (const [queryKey, page] of qc.getQueriesData<OffsetPage<Repository>>({
    queryKey: ["repos"],
  })) {
    if (!page) continue;

    const [, search = "", status = "all"] = queryKey as [string, string?, string?];
    const normalizedSearch = search.toLowerCase();
    if (normalizedSearch && !repoSearch.includes(normalizedSearch)) continue;
    if (status !== "all" && status !== "pending") continue;

    const alreadyPresent = page.items.some((item) => item.id === repo.id);
    const nextTotal = alreadyPresent ? page.total : page.total + 1;

    qc.setQueryData<OffsetPage<Repository>>(queryKey, {
      ...page,
      items: [placeholder, ...page.items.filter((item) => item.id !== repo.id)].slice(
        0,
        page.per_page,
      ),
      total: nextTotal,
      total_pages: Math.max(1, Math.ceil(nextTotal / page.per_page)),
    });
  }
}

/**
 * Repo-list query. Polls every 3s so in-flight repos visibly advance without a
 * manual refresh. Capability-disabled first-run placeholders skip synthetic
 * embed/generate stages so the grid does not overstate skipped work.
 */
export function useRepos(params?: {
  search?: string;
  status?: Repository["status"];
}) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["repos", params?.search ?? "", params?.status ?? "all"],
    queryFn: async () => {
      const qs = new URLSearchParams();
      if (params?.search) qs.set("search", params.search);
      if (params?.status) qs.set("status", params.status);
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      return apiJson<OffsetPage<Repository>>(`/api/repos${suffix}`);
    },
    select: (data) => ({
      ...data,
      items: data.items.map((repo) => applyCreatedRepoLifecycle(qc, repo)),
    }),
    refetchInterval: (q) => {
      const data = q.state.data as OffsetPage<Repository> | undefined;
      // Poll while anything is mid-pipeline OR a re-sync is active. The latter
      // is load-bearing: a re-sync of a READY repo keeps status "ready", so
      // isInFlightRepoStatus alone would never clear the sync_state badge.
      const inFlight = data?.items.some((r) => isInFlightRepoStatus(r.status) || !!r.sync_state);
      return inFlight || hasActiveCreatedRepoLifecycle(qc) ? 1000 : false;
    },
  });
  return query;
}

/**
 * Single-repo detail keyed by the compound slug `host/owner/name`. Polls at
 * the same cadence as the list while the repo is mid-pipeline.
 */
export function useRepo(slug: RepoSlug | null | undefined) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["repo", slug?.host, slug?.owner, slug?.name],
    enabled: !!slug,
    queryFn: async () => {
      if (!slug) throw new Error("repo slug missing");
      return apiJson<Repository>(repoApiPath(slug));
    },
    select: (repo) => applyCreatedRepoLifecycle(qc, repo),
    refetchInterval: (q) => {
      const data = q.state.data as Repository | undefined;
      if (!data) return false;
      return isInFlightRepoStatus(data.status) ||
        !!data.sync_state ||
        hasActiveCreatedRepoLifecycle(qc, data.id)
        ? 1000
        : false;
    },
  });
}

/** Create-repo mutation. Invalidates the list on success. */
export function useCreateRepo() {
  const qc = useQueryClient();
  // onMutate generates a fresh UUID for each new submission; the same ref value
  // is reused across TanStack auto-retries because onMutate is not called on retry.
  const idempotencyKeyRef = useRef<string | null>(null);

  return useMutation({
    onMutate: () => {
      idempotencyKeyRef.current = crypto.randomUUID();
    },
    mutationFn: async (input: SubmitRepoRequest) => {
      return apiJson<Repository>("/api/repos", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKeyRef.current ?? crypto.randomUUID(),
        },
        body: JSON.stringify(input),
      });
    },
    onSuccess: (createdRepo) => {
      setCreatedRepoLifecycle(qc, createdRepo.id);
      qc.setQueryData(
        ["repo", createdRepo.host, createdRepo.owner, createdRepo.name],
        applyFirstRunPlaceholder(createdRepo, "pending"),
      );
      seedCreatedRepoIntoListCaches(qc, createdRepo);
      qc.invalidateQueries({ queryKey: ["repos"] });
    },
  });
}

type ReindexResponse = { id: string; status: string };

/**
 * Reindex-repo mutation. Triggers `POST /repos/<slug>/reindex` which enqueues a
 * fresh sync run for git-backed repos; the detail/list polling already in
 * `useRepo` / `useRepos` then picks up the new pending status.
 */
export function useReindexRepo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (slug: RepoSlug) =>
      apiJson<ReindexResponse>(repoApiPath(slug, "reindex"), { method: "POST" }),
    onSuccess: (_data, slug) => {
      qc.invalidateQueries({ queryKey: ["repo", slug.host, slug.owner, slug.name] });
      qc.invalidateQueries({ queryKey: ["repos"] });
    },
  });
}

/** Delete-repo mutation. Optimistically removes from cache. */
export function useDeleteRepo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (slug: RepoSlug) => {
      await apiFetch(repoApiPath(slug), { method: "DELETE" });
      return slug;
    },
    onSuccess: (slug) => {
      qc.invalidateQueries({ queryKey: ["repos"] });
      qc.removeQueries({ queryKey: ["repo", slug.host, slug.owner, slug.name] });
    },
  });
}
