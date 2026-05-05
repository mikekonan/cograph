import type { LinkedIdentity } from "@/api/identities";

export const seedMyIdentities: LinkedIdentity[] = [
  {
    id: "uid-okta-0001",
    provider_id: "idp-okta-0001",
    provider_slug: "okta",
    provider_display_name: "Okta",
    subject: "00uxxxxxxxx",
    email_at_link: "admin@example.com",
    last_login_at: "2026-05-03T08:42:11Z",
    created_at: "2026-04-22T10:15:00Z",
  },
];
