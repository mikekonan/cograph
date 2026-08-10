import type { RepoStatus, Repository } from "@/api/types";

export const IN_FLIGHT_REPO_STATUSES: RepoStatus[] = [
  "pending",
  "cloning",
  "indexing",
  "embedding",
  "generating",
];

const FIRST_RUN_LIFECYCLE_SEQUENCE: Array<{ status: RepoStatus; durationMs: number }> = [
  { status: "pending", durationMs: 1_000 },
  { status: "cloning", durationMs: 1_500 },
  { status: "indexing", durationMs: 2_000 },
  { status: "embedding", durationMs: 1_500 },
  { status: "generating", durationMs: 1_500 },
];

export function getFirstRunLifecycleTotalMs(): number {
  return FIRST_RUN_LIFECYCLE_SEQUENCE.reduce((total, step) => total + step.durationMs, 0);
}

export const FIRST_RUN_LIFECYCLE_TOTAL_MS = getFirstRunLifecycleTotalMs();

export function isInFlightRepoStatus(status: RepoStatus): boolean {
  return IN_FLIGHT_REPO_STATUSES.includes(status);
}

export function getFirstRunLifecycleStatus(
  startedAtMs: number,
  nowMs: number = Date.now(),
): RepoStatus | null {
  const elapsed = Math.max(0, nowMs - startedAtMs);
  let cumulative = 0;

  for (const step of FIRST_RUN_LIFECYCLE_SEQUENCE) {
    cumulative += step.durationMs;
    if (elapsed < cumulative) return step.status;
  }

  return null;
}

export function repoInFlightMessage(status: RepoStatus): string | null {
  switch (status) {
    case "pending":
      return "Queued for the first indexing pass.";
    case "cloning":
      return "Cloning the repository checkout.";
    case "indexing":
      return "Parsing files and building the code graph.";
    case "embedding":
      return "Embedding code and repository documents.";
    case "generating":
      return "Generating summaries and wiki pages.";
    default:
      return null;
  }
}

export function applyFirstRunPlaceholder(repo: Repository, status: RepoStatus): Repository {
  if (!isInFlightRepoStatus(status)) return repo;

  return {
    ...repo,
    status,
    last_commit: null,
    last_synced_at: null,
    readme: null,
    stats: {
      ...repo.stats,
      languages: [],
      language_bytes: undefined,
      modules_count: 0,
      functions_count: 0,
      classes_count: 0,
      documents_count: 0,
      total_nodes: 0,
      source_files: 0,
    },
  };
}
