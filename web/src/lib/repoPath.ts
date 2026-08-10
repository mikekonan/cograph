import type { RepoSlug } from "@/api/types";

/**
 * Build the FE route path for a repository. Repositories are addressed by
 * their compound slug `host/owner/name`, e.g. `/repos/github.com/mikekonan/cograph`.
 *
 * Pass extra path segments to append (already-encoded — slugs only contain
 * `[A-Za-z0-9._-]` so URL encoding is a no-op for them, but page slugs can
 * contain non-ASCII characters and must be encoded by the caller).
 */
export function repoPath(repo: RepoSlug, ...rest: string[]): string {
  const trail = rest.length > 0 ? `/${rest.join("/")}` : "";
  return `/repos/${repo.host}/${repo.owner}/${repo.name}${trail}`;
}

/**
 * Same as `repoPath`, but for backend REST URLs under `/api/repos/...`.
 * `apiFetch` prepends nothing else, so callers should use the value
 * verbatim.
 */
export function repoApiPath(repo: RepoSlug, ...rest: string[]): string {
  const trail = rest.length > 0 ? `/${rest.join("/")}` : "";
  return `/api/repos/${repo.host}/${repo.owner}/${repo.name}${trail}`;
}

/**
 * Resolve a `RepoSlug` from the `useParams()` object on a route declared
 * as `repos/:host/:owner/:name/...`. Returns null when any component is
 * missing — callers should render a NotFound state in that case.
 */
export function parseSlugFromParams(params: {
  host?: string;
  owner?: string;
  name?: string;
}): RepoSlug | null {
  const { host, owner, name } = params;
  if (!host || !owner || !name) return null;
  return { host, owner, name };
}

/**
 * Best-effort client-side parser for a git URL. Mirrors the backend
 * `_parse_host_owner_and_name` for the live preview shown in
 * AddRepoDialog.
 *
 * Returns null when the URL is not a recognised git form. Strips trailing
 * `.git` and collapses multi-segment paths to the last two (matching
 * backend behaviour for GitLab subgroups).
 */
export function parseGitUrl(rawUrl: string): RepoSlug | null {
  const url = rawUrl.trim();
  if (!url) return null;

  let host: string;
  let path: string;

  if (url.startsWith("git@")) {
    // SCP-like SSH: git@github.com:owner/repo[.git]
    const at = url.indexOf("@");
    const colon = url.indexOf(":");
    if (at < 0 || colon < at) return null;
    host = url.slice(at + 1, colon);
    path = url.slice(colon + 1);
  } else {
    try {
      const parsed = new URL(url);
      host = parsed.hostname;
      path = parsed.pathname;
    } catch {
      return null;
    }
  }

  let normalisedPath = path.replace(/\/+$/g, "");
  if (normalisedPath.endsWith(".git")) {
    normalisedPath = normalisedPath.slice(0, -".git".length);
  }
  const parts = normalisedPath.split("/").filter(Boolean);
  if (!host || parts.length < 2) return null;
  return { host, owner: parts[parts.length - 2], name: parts[parts.length - 1] };
}

const HOST_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,253}[A-Za-z0-9])?$/;
const SEGMENT_RE = /^[A-Za-z0-9._-]{1,100}$/;

/**
 * Validate a slug component against the same rules the backend enforces.
 * Used by AddRepoDialog's ZIP form to disable the submit button until
 * each field is valid (avoids round-tripping a 422 to the user).
 */
export function isValidHost(value: string): boolean {
  return HOST_RE.test(value);
}

export function isValidRepoSegment(value: string): boolean {
  return SEGMENT_RE.test(value);
}
