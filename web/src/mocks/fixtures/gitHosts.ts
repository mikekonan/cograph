import type { GitCredentialView, GitHostView, WebhookDeliveryView } from "@/api/gitHosts";

export const seedGitHosts: GitHostView[] = [
  {
    id: "host-github-com",
    slug: "github-com",
    display_name: "GitHub.com",
    kind: "github",
    base_url: "https://github.com",
    api_url: "https://api.github.com",
    git_host: "github.com",
    enabled: true,
    default_credential_id: "cred-github-default",
    created_at: "2026-04-15T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
  },
];

export const seedGitCredentials: GitCredentialView[] = [
  {
    id: "cred-github-default",
    host_id: "host-github-com",
    label: "Demo operator",
    token_prefix: "ghp_demo1234",
    scopes_observed: ["repo", "read:org"],
    is_default: true,
    last_tested_at: "2026-05-04T07:30:00Z",
    last_test_status: "ok",
    last_test_error: null,
    has_webhook_secret: true,
    created_at: "2026-04-15T10:00:00Z",
    updated_at: "2026-05-04T07:30:00Z",
  },
];

export const seedWebhookDeliveries: WebhookDeliveryView[] = [
  {
    id: "wh-0001",
    host_id: "host-github-com",
    delivery_id: "evt-aaaa-1111",
    repo_full_name: "mikekonan/cograph",
    event: "push",
    received_at: "2026-05-05T08:30:00Z",
    sync_job_id: "sync-1234",
  },
  {
    id: "wh-0002",
    host_id: "host-github-com",
    delivery_id: "evt-bbbb-2222",
    repo_full_name: "mikekonan/cograph",
    event: "ping",
    received_at: "2026-05-05T08:25:00Z",
    sync_job_id: null,
  },
];
