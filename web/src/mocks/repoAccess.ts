import type { Repository } from "@/api/types";
import { mockAuth, mockDb, mockRuntime } from "@/mocks/state";

export function canReadMockRepo(repo: Repository): boolean {
  if (mockAuth.isAdmin) {
    return true;
  }

  return mockRuntime.publicRead && repo.visibility === "public";
}

export function listReadableMockRepos(): Repository[] {
  return mockDb.repos.filter((repo) => canReadMockRepo(repo));
}

export function getReadableMockRepo(repoId: string): Repository | null {
  const repo = mockDb.repos.find((entry) => entry.id === repoId);
  if (!repo || !canReadMockRepo(repo)) {
    return null;
  }
  return repo;
}

export function getReadableMockRepoBySlug(
  host: string,
  owner: string,
  name: string,
): Repository | null {
  const repo = mockDb.repos.find(
    (entry) => entry.host === host && entry.owner === owner && entry.name === name,
  );
  if (!repo || !canReadMockRepo(repo)) {
    return null;
  }
  return repo;
}
