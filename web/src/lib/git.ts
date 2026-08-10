/**
 * Git-host URL helpers. Given the canonical git clone URL of a repo, build
 * the public web URL that navigates to a specific file + line range.
 *
 * Supports github.com, gitlab.com, bitbucket.org — our indexer ingests any
 * git host, but these three cover >95% of public repos. Unknown hosts fall
 * back to a best-effort GitHub-like path (often still works on self-hosted
 * Gitea / Forgejo).
 */

export type GitHost = "github" | "gitlab" | "bitbucket" | "unknown";

/** Normalise `git_url` into {host, baseUrl, ownerRepo}. */
export function parseGitHost(gitUrl: string): {
  host: GitHost;
  baseUrl: string;
  ownerRepo: string;
} | null {
  const cleaned = gitUrl
    .trim()
    .replace(/\.git$/, "")
    // SSH form: git@github.com:owner/repo
    .replace(/^git@([^:]+):/, "https://$1/");

  try {
    const url = new URL(cleaned);
    const host = hostFromDomain(url.hostname);
    const ownerRepo = url.pathname.replace(/^\//, "");
    const baseUrl = `${url.protocol}//${url.host}/${ownerRepo}`;
    return { host, baseUrl, ownerRepo };
  } catch {
    return null;
  }
}

function hostFromDomain(hostname: string): GitHost {
  if (hostname.endsWith("github.com")) return "github";
  if (hostname.endsWith("gitlab.com") || hostname.startsWith("gitlab.")) return "gitlab";
  if (hostname.endsWith("bitbucket.org")) return "bitbucket";
  return "unknown";
}

/**
 * Build a source URL pointing at `{path}` on `{branch}` inside the repo,
 * optionally anchored at a line range like "15-30" or a single line "42".
 *
 * Returns null when `gitUrl` doesn't parse — callers should render the
 * pill as non-clickable in that case rather than producing a bad link.
 */
export function buildSourceUrl(
  gitUrl: string,
  branch: string,
  path: string,
  lines?: string,
): string | null {
  const parsed = parseGitHost(gitUrl);
  if (!parsed) return null;

  const { host, baseUrl } = parsed;
  const cleanPath = path.replace(/^\/+/, "");

  // Host-specific path + anchor conventions.
  switch (host) {
    case "github":
    case "unknown":
      return `${baseUrl}/blob/${encodeURIComponent(branch)}/${cleanPath}${buildGithubAnchor(lines)}`;
    case "gitlab":
      return `${baseUrl}/-/blob/${encodeURIComponent(branch)}/${cleanPath}${buildGitlabAnchor(lines)}`;
    case "bitbucket":
      return `${baseUrl}/src/${encodeURIComponent(branch)}/${cleanPath}${buildBitbucketAnchor(lines)}`;
  }
}

function buildGithubAnchor(lines?: string): string {
  if (!lines) return "";
  const m = lines.match(/^(\d+)(?:-(\d+))?$/);
  if (!m) return "";
  return m[2] ? `#L${m[1]}-L${m[2]}` : `#L${m[1]}`;
}

function buildGitlabAnchor(lines?: string): string {
  if (!lines) return "";
  // GitLab uses #L15-30 (single L).
  const m = lines.match(/^(\d+)(?:-(\d+))?$/);
  if (!m) return "";
  return m[2] ? `#L${m[1]}-${m[2]}` : `#L${m[1]}`;
}

function buildBitbucketAnchor(lines?: string): string {
  if (!lines) return "";
  const m = lines.match(/^(\d+)(?:-(\d+))?$/);
  if (!m) return "";
  // Bitbucket uses #lines-15:30 (colon).
  return m[2] ? `#lines-${m[1]}:${m[2]}` : `#lines-${m[1]}`;
}
