# Security Policy

## Supported versions

Cograph is pre-1.0 and ships from `main`. Security fixes land on `main`
and are not backported to older tags.

| Version | Supported          |
|---------|--------------------|
| `main`  | :white_check_mark: |
| Tagged pre-1.0 releases | :x: |

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security reports.

Instead, file a private report through GitHub Security Advisories:

- <https://github.com/mikekonan/cograph/security/advisories/new>

Include in your report:

- A description of the issue and the impact you observed.
- A minimal proof-of-concept (logs, request samples, code snippet) so we
  can reproduce the problem.
- The commit SHA or tag you tested against.
- Whether you believe the issue is being actively exploited in the wild.

## What to expect

- We will acknowledge receipt within **3 business days**.
- We will share an initial assessment (severity + planned remediation
  path) within **10 business days**.
- We will keep you updated as we work toward a fix and will credit you in
  the release notes once the fix is public, unless you prefer to remain
  anonymous.
- If the issue is confirmed, we will coordinate a disclosure window with
  you (typically 30–90 days depending on severity and complexity)
  before any public advisory.

## Scope

In scope:

- The Cograph backend API, worker, and MCP server (`backend/`).
- The Cograph web client (`web/`).
- The packaged Docker / Helm artifacts and the published GitHub Actions
  workflows.

Out of scope:

- Third-party services Cograph integrates with (LLM providers, git hosts,
  Postgres, Redis). Report those directly to the upstream vendor.
- Vulnerabilities that require a malicious local administrator account
  with full admin permissions (the admin role is intentionally
  high-trust).
- Self-DoS scenarios where a legitimate operator misconfigures resource
  limits.

## Hall of fame

We are happy to publicly credit reporters who follow this policy. We
will credit you in the published GitHub Security Advisory unless you
prefer to remain anonymous.
