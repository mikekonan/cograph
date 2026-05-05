import type { IdentityProvider } from "@/api/identityProviders";

export const seedIdentityProviders: IdentityProvider[] = [
  {
    id: "idp-okta-0001",
    slug: "okta",
    display_name: "Okta",
    kind: "oidc",
    enabled: true,
    issuer: "https://example.okta.com",
    client_id: "0oamockclientid",
    client_secret_configured: true,
    scopes: ["openid", "profile", "email"],
    response_mode: "code",
    domain_allowlist: ["example.com"],
    default_role: "user",
    admin_group: "cograph-admins",
    admin_group_mode: "owner_approval",
    claim_mappings: { email: "email", name: "name" },
    created_at: "2026-04-15T08:00:00Z",
    updated_at: "2026-05-01T09:30:00Z",
  },
];
