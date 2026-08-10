import type { ScimClientView, ScimEventView } from "@/api/scim";

export const seedScimClients: ScimClientView[] = [
  {
    id: "scim-client-0001",
    provider_id: "idp-okta-0001",
    provider_slug: "okta",
    name: "Okta SCIM",
    token_prefix: "cgr_pat_demo01",
    scopes: ["users:write"],
    revoked_at: null,
    revoked_reason: null,
    last_used_at: "2026-05-04T07:50:11Z",
    last_used_ip: "203.0.113.42",
    created_at: "2026-04-20T10:00:00Z",
  },
];

export const seedScimEvents: ScimEventView[] = [
  {
    id: "scim-event-0003",
    client_id: "scim-client-0001",
    provider_id: "idp-okta-0001",
    operation: "patch",
    external_id: "00ualice",
    target_user_id: "user-alice",
    status: "applied",
    error_code: null,
    applied_at: "2026-05-04T07:50:11Z",
  },
  {
    id: "scim-event-0002",
    client_id: "scim-client-0001",
    provider_id: "idp-okta-0001",
    operation: "patch",
    external_id: "00ualice",
    target_user_id: "user-alice",
    status: "no_op",
    error_code: null,
    applied_at: "2026-05-04T07:50:09Z",
  },
  {
    id: "scim-event-0001",
    client_id: "scim-client-0001",
    provider_id: "idp-okta-0001",
    operation: "create",
    external_id: "00ucharlie",
    target_user_id: "user-charlie",
    status: "applied",
    error_code: null,
    applied_at: "2026-05-03T13:22:51Z",
  },
];
