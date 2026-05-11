import type { IdentityProvider } from "@/api/identityProviders";

export const seedIdentityProviders: IdentityProvider[] = [
  {
    id: "idp-okta-0001",
    slug: "okta",
    display_name: "Okta",
    kind: "oidc",
    enabled: true,
    issuer_url: "https://example.okta.com",
    client_id: "0oamockclientid",
    has_client_secret: true,
    scopes: ["openid", "profile", "email"],
    response_mode: "query",
    groups_claim: null,
    domain_allowlist: ["example.com"],
    auto_provision: true,
    admin_groups: ["cograph-admins"],
    admin_group_mode: "owner_approval",
    created_at: "2026-04-15T08:00:00Z",
    updated_at: "2026-05-01T09:30:00Z",
  },
];
