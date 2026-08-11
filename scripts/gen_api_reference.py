#!/usr/bin/env python3
"""Generate the public REST reference from the app's own OpenAPI schema.

    python scripts/gen_api_reference.py            # write docs/api-reference.md
    python scripts/gen_api_reference.py --check    # fail if it is out of date

Why generated: the OpenAPI schema is served only in development, so a reader of
the public documentation site cannot reach it. Hand-maintaining 138 operations
against it would drift within a week. Generating means the reference cannot be
wrong about a path, a method, a parameter or a status code — it is the schema.

Why committed rather than built on the docs site: the docs build is pure Node and
should not need a Python environment with the backend installed. CI regenerates
this file in the backend job (which already has the dependencies) and fails if the
result differs, so an API change and its documentation land in the same commit.

Deliberately not emitted: full request/response field tables. That would be a
20,000-line page nobody reads, and the model names below are the searchable
handle — `CreateRepositoryRequest` is enough to find the schema in the code. The
goal is "an engineer knows the operation exists, how to call it, and what it
returns", not a second copy of the type definitions.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "api-reference.md"

# Human ordering and titles for the schema's tags. Anything not listed still gets
# rendered, under its raw tag, at the end — so a new router shows up rather than
# silently vanishing.
TAG_GROUPS: list[tuple[str, str, str]] = [
    (
        "health",
        "Health",
        "Liveness. Mounted twice so probes need not know the API prefix.",
    ),
    (
        "auth",
        "Authentication",
        "Session lifecycle: config, first-admin bootstrap, register, login, logout, refresh — plus the OIDC login, callback and account-linking routes.",
    ),
    ("me", "Current user", "The caller's own identities and query history."),
    (
        "tokens",
        "Personal access tokens",
        "Mint, list, rotate and revoke tokens for REST and MCP.",
    ),
    (
        "repos",
        "Repositories",
        "Create, configure, index and delete repositories, including zip upload.",
    ),
    (
        "wiki",
        "Generated wiki",
        "Read the generated page tree and individual pages; repair stale citations.",
    ),
    ("docs", "Repository docs", "The repository's own in-tree markdown, rendered."),
    (
        "repo-documents",
        "Repository documents",
        "The indexed document rows behind the docs tree.",
    ),
    (
        "retrieval",
        "Retrieval",
        "Hybrid search — the REST mirror of the MCP retrieve tool.",
    ),
    (
        "route",
        "Source routing",
        "Which repository or collection likely holds the answer.",
    ),
    (
        "md-collections",
        "Markdown collections",
        "Uploaded document corpora: CRUD, upload, search, embedding and jobs.",
    ),
    ("jobs", "Pipeline jobs", "Sync batches, per-step jobs, retry and cancel."),
    (
        "query-logs",
        "Query logs",
        "Usage analytics for admins, and self-service history.",
    ),
    (
        "admin",
        "Administration",
        "One router tag covers the whole admin surface: users, groups and their "
        "repository/collection grants, git hosts and clone credentials, identity "
        "providers, SCIM clients and the SCIM event log.",
    ),
    (
        "admin-llm-runtime",
        "LLM runtime",
        "Per-role model assignment, embedding state and provider tests.",
    ),
    ("admin-secrets", "Provider secrets", "Encrypted provider credentials."),
    (
        "admin-mcp",
        "MCP briefing",
        "The operator briefing injected into every MCP session.",
    ),
    ("webhooks", "Inbound webhooks", "Push events that trigger a sync."),
    (
        "scim",
        "SCIM 2.0",
        "User provisioning. Mounted at /scim/v2, outside the API prefix.",
    ),
]

METHOD_ORDER = ["get", "post", "put", "patch", "delete"]


def load_schema() -> dict:
    """Import the app and return its OpenAPI document.

    `is_development` gates whether the schema is *served*; `app.openapi()` builds
    it regardless, so this works without a database or any provider configured.
    """
    os.environ.setdefault("COGRAPH_ENVIRONMENT", "development")
    sys.path.insert(0, str(REPO_ROOT))
    from backend.app.main import app  # noqa: PLC0415 — import after env setup

    return app.openapi()


def schema_name(node: dict | None) -> str | None:
    """Best-effort model name for a request/response schema node."""
    if not node:
        return None
    if "$ref" in node:
        return node["$ref"].rsplit("/", 1)[-1]
    for key in ("items", "anyOf", "allOf", "oneOf"):
        value = node.get(key)
        if isinstance(value, dict):
            inner = schema_name(value)
            if inner:
                return f"{inner}[]" if key == "items" else inner
        if isinstance(value, list):
            for candidate in value:
                inner = schema_name(candidate)
                if inner and inner != "null":
                    return inner
    if node.get("type") == "array":
        inner = schema_name(node.get("items"))
        return f"{inner}[]" if inner else "array"
    return node.get("type")


def body_model(op: dict) -> str:
    content = (op.get("requestBody") or {}).get("content") or {}
    for media, spec in content.items():
        # FastAPI names a form body `Body_<operation_id>`, which carries no
        # information a reader can act on — say what it is instead.
        if media.startswith("multipart/"):
            return "form data"
        name = schema_name(spec.get("schema"))
        if name:
            return f"`{name}`" if media == "application/json" else f"`{name}` ({media})"
    return "—"


def response_model(op: dict) -> str:
    responses = op.get("responses") or {}
    for status in sorted(responses):
        if not status.startswith("2"):
            continue
        content = (responses[status] or {}).get("content") or {}
        for spec in content.values():
            name = schema_name(spec.get("schema"))
            if name:
                return f"`{status}` `{name}`"
        return f"`{status}`"
    return "—"


def params(op: dict) -> str:
    """Query and header parameters, path parameters excluded.

    Path parameters are already visible in the path itself, so repeating them
    doubles the width of every row for no information.
    """
    out = []
    for p in op.get("parameters") or []:
        if p.get("in") == "path":
            continue
        name = p.get("name", "")
        # Cross-cutting headers, not per-operation inputs. `authorization` is
        # declared explicitly on every SCIM route, which would otherwise fill
        # that whole table with one repeated word.
        if name.lower() in {"x-csrf-token", "idempotency-key", "authorization"}:
            continue
        out.append(f"`{name}`" + ("*" if p.get("required") else ""))
    return ", ".join(out) if out else "—"


def summary(op: dict) -> str:
    """First sentence of the handler's docstring, else its generated summary.

    Order matters: FastAPI always synthesises `summary` from the function name
    ("List Repositories"), which mostly restates the path. A docstring, where one
    exists, says something the path does not — so it wins.
    """
    doc = (op.get("description") or "").strip()
    text = (
        doc.split("\n")[0].split(". ")[0] if doc else (op.get("summary") or "").strip()
    )
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text or "—"


def render(schema: dict) -> str:
    paths: dict = schema["paths"]

    by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path, ops in sorted(paths.items()):
        for method in METHOD_ORDER:
            op = ops.get(method)
            if not isinstance(op, dict):
                continue
            tag = (op.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method, path, op))

    known = {tag for tag, _, _ in TAG_GROUPS}
    ordered = list(TAG_GROUPS) + [
        (tag, tag.replace("-", " ").title(), "")
        for tag in sorted(by_tag)
        if tag not in known
    ]

    total_ops = sum(len(v) for v in by_tag.values())

    lines = [
        "---",
        "# Generated by scripts/gen_api_reference.py — do not edit by hand.",
        "editLink: false",
        "---",
        "",
        "# REST reference",
        "",
        f"Every endpoint the backend exposes: **{total_ops} operations** across "
        f"**{len(paths)} paths**, generated from the application's own OpenAPI "
        "schema so it cannot drift from the code.",
        "",
        "See [REST API](/api) for authentication, error shapes, and how the "
        "interactive schema is exposed. Request and response columns name the "
        "model — search the backend for that name to see its fields.",
        "",
        "::: info Reading the tables",
        "A `*` marks a required parameter. Path parameters are omitted from the "
        "parameters column because they are visible in the path, and the "
        "`Authorization`, `X-CSRF-Token` and `Idempotency-Key` headers are omitted "
        "because they are cross-cutting rather than per-operation.",
        "",
        "A `—` in the request column on a `POST`, `PUT` or `PATCH` means the handler "
        "reads the raw request body itself instead of declaring a model, so the "
        "schema has nothing to report. The upload, OIDC-callback and webhook routes "
        "work that way; each one's accepted shape is described on the page for its "
        "feature.",
        ":::",
        "",
    ]

    for tag, title, blurb in ordered:
        entries = by_tag.get(tag)
        if not entries:
            continue
        lines.append(f"## {title}")
        lines.append("")
        if blurb:
            lines.append(blurb)
            lines.append("")
        lines.append("| Operation | Purpose | Parameters | Request | Response |")
        lines.append("| --- | --- | --- | --- | --- |")
        for method, path, op in entries:
            deprecated = " *(deprecated)*" if op.get("deprecated") else ""
            lines.append(
                f"| `{method.upper()} {path}`{deprecated} | {summary(op)} "
                f"| {params(op)} | {body_model(op)} | {response_model(op)} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed file is out of date",
    )
    args = parser.parse_args()

    rendered = render(load_schema())

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            print(
                f"{OUTPUT.relative_to(REPO_ROOT)} is out of date.\n"
                "The REST API changed without regenerating the reference. Run:\n"
                "    python scripts/gen_api_reference.py\n"
                "and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.relative_to(REPO_ROOT)} is up to date.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
