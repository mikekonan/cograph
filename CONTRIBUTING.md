# Contributing to Cograph

Thanks for your interest in Cograph. This guide is the short, actionable
version for contributing to the public repository.

## Code of Conduct

Participation in this project is governed by the
[Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By contributing,
you agree to abide by it.

## Reporting bugs and requesting features

Open an issue with one of these shapes:

- **Bug** — what you expected, what happened, exact steps to reproduce, and
  the version/commit you saw it on. Include logs or screenshots when relevant.
- **Feature request** — the problem you are trying to solve (not just the
  solution you have in mind), and at least one alternative you considered.

Please search existing issues first; duplicates get closed.

If you found a security vulnerability, **do not open a public issue** —
follow the process in [`.github/SECURITY.md`](.github/SECURITY.md).

## Development setup

Cograph runs as a multi-service stack. The supported entrypoint is Docker
Compose:

```bash
docker compose up --build
# Web UI at http://localhost:8080
```

For frontend-only development with mocks, see the quick-start section in
the root `README.md`.

## Code style

- **Frontend (`web/`)**: TypeScript strict, Biome for lint+format, Tailwind v4
  with `@theme` tokens (no raw scales in components). Run `npm run typecheck
  && npm run lint && npm run test && npm run build` before pushing.
- **Backend (`backend/`)**: Python 3.12, Ruff for lint+format, FastAPI +
  SQLAlchemy 2.0 (async), `pytest`. Run `ruff check backend/ && pytest
  backend/tests` before pushing.
- API payloads stay `snake_case` end-to-end — do not auto-convert to
  camelCase in the client.
- Design tokens are semantic only (`bg-[color:var(--color-bg-surface)]`) —
  raw `stone-*` / `copper-*` scales live inside `@theme`.

## Pull request flow

1. **Open an issue first** for non-trivial changes so we can agree on scope
   before you write code. Drive-by typo fixes and tiny cleanups are fine
   without an issue.
2. **Fork** the repo, create a topic branch off `main`
   (`fix/<short-slug>` or `feat/<short-slug>`).
3. **Keep PRs small and focused.** One concern per PR. Refactors that touch
   many files should be a separate PR from behavior changes.
4. **Keep public docs accurate.** If your change affects setup, user-facing
   behavior, public API shape, or deployment, update `README.md` or inline
   comments in the same PR.
5. **Run the gates** before pushing:
   - `cd web && npm run typecheck && npm run lint && npm run test && npm run build`
   - `cd backend && ruff check . && pytest tests`
6. **Commit messages**: imperative mood, conventional prefix (`feat:`,
   `fix:`, `docs:`, `chore:`, `refactor:`), one-line header under 72 chars,
   then a bullet body explaining *what* and *why* at a high level.
7. **Open the PR** against `main`. Fill in the template (what, why, how
   tested, any breaking changes).

## What we do not accept

- Changes that introduce a new state manager, styling framework, build tool,
  or package manager without prior discussion.
- Vendor-branded colors / logos / asset names.
- Backwards-compatibility shims for hypothetical or external consumers —
  Cograph is pre-1.0 and we ship breaking changes when they make the code
  better.
- Features without tests. Every new endpoint, hook, or component needs
  matching coverage.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE) — the same license that covers the rest of
the project.
