import type { Repository } from "@/api/types";
import { describe, expect, it } from "vitest";
import {
  FIRST_RUN_LIFECYCLE_TOTAL_MS,
  applyFirstRunPlaceholder,
  getFirstRunLifecycleStatus,
  repoInFlightMessage,
} from "../repoStatus";

const READY_REPO = {
  id: "repo-1",
  git_url: "https://github.com/test/repo",
  source: "git" as const,
  host: "github.com",
  name: "repo",
  owner: "test",
  branch: "main",
  status: "ready" as const,
  last_commit: "abc1234",
  error_msg: null,
  readme: "# README",
  stats: {
    languages: ["typescript"],
    language_bytes: { typescript: 42_000 },
    modules_count: 12,
    functions_count: 34,
    classes_count: 5,
    documents_count: 3,
    total_nodes: 51,
    source_files: 8,
  },
  sync_schedule: "manual" as const,
  visibility: "public" as const,
  last_synced_at: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} satisfies Repository;

describe("repoStatus helpers", () => {
  it("maps the first-run lifecycle across the expected visible statuses", () => {
    const startedAt = 10_000;

    expect(getFirstRunLifecycleStatus(startedAt, startedAt)).toBe("pending");
    expect(getFirstRunLifecycleStatus(startedAt, startedAt + 1_100)).toBe("cloning");
    expect(getFirstRunLifecycleStatus(startedAt, startedAt + 2_700)).toBe("indexing");
    expect(getFirstRunLifecycleStatus(startedAt, startedAt + 4_900)).toBe("embedding");
    expect(getFirstRunLifecycleStatus(startedAt, startedAt + 6_100)).toBe("generating");
    expect(
      getFirstRunLifecycleStatus(startedAt, startedAt + FIRST_RUN_LIFECYCLE_TOTAL_MS + 1),
    ).toBeNull();
  });

  it("turns a ready repo into an in-flight placeholder while the first-run lifecycle is visible", () => {
    const pendingRepo = applyFirstRunPlaceholder(READY_REPO, "pending");

    expect(pendingRepo.status).toBe("pending");
    expect(pendingRepo.last_commit).toBeNull();
    expect(pendingRepo.last_synced_at).toBeNull();
    expect(pendingRepo.readme).toBeNull();
    expect(pendingRepo.stats.modules_count).toBe(0);
    expect(pendingRepo.stats.documents_count).toBe(0);
    expect(pendingRepo.stats.languages).toEqual([]);
  });

  it("returns status-specific user-facing copy for in-flight repos", () => {
    expect(repoInFlightMessage("pending")).toMatch(/queued/i);
    expect(repoInFlightMessage("embedding")).toMatch(/embedding code/i);
    expect(repoInFlightMessage("generating")).toMatch(/generating summaries/i);
    expect(repoInFlightMessage("indexing")).toMatch(/parsing files/i);
    expect(repoInFlightMessage("ready")).toBeNull();
  });
});
