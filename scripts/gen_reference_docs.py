#!/usr/bin/env python3
"""Generate the public REST and MCP references from the application itself.

    python scripts/gen_reference_docs.py            # write both pages
    python scripts/gen_reference_docs.py --check    # fail if either is stale

Two pages, one script and one CI step, because both are the same job: take a
machine-readable contract the running server already publishes and render it for
a human. `docs/api-reference.md` comes from FastAPI's OpenAPI document;
`docs/mcp-reference.md` comes from the MCP server's own `list_tools()`.

Why generated: neither contract is reachable by a reader of the public site. The
OpenAPI schema is served only in development, and the tool list only over an
authenticated MCP session. Hand-maintaining 138 operations and 14 tool signatures
against them drifts within a week — generating means the reference cannot be wrong
about a path, a parameter, a default or a bound.

Why committed rather than built on the docs site: the docs build is pure Node and
should not need a Python environment with the backend installed. CI regenerates
both files in the backend job (which already has the dependencies) and fails if
either differs, so an API change and its documentation land in the same commit.

Deliberately not emitted: full request/response field tables for REST. That would
be a 20,000-line page nobody reads, and the model names are the searchable handle
— `CreateRepositoryRequest` is enough to find the schema in the code. The goal is
"an engineer knows the operation exists, how to call it, and what it returns", not
a second copy of the type definitions. MCP tools are different and do get full
parameter tables: an agent has to construct the call from the signature alone.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_OUTPUT = REPO_ROOT / "docs" / "api-reference.md"
MCP_OUTPUT = REPO_ROOT / "docs" / "mcp-reference.md"

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

# Ordered presentation of the MCP tools, matching the decision ladder an agent is
# told to follow: find the source, search it, read what you found, then traverse.
TOOL_GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "Orientation",
        "Called first, to find out what exists and where to look. Cheap: none of "
        "these returns file contents.",
        [
            "cograph_repositories",
            "cograph_collections",
            "cograph_route",
            "cograph_outline",
            "cograph_repository_readme",
        ],
    ),
    (
        "Search",
        "Ranked candidates within one source. Returns snippets, not whole files.",
        ["cograph_retrieve", "cograph_search_code", "cograph_collection_search"],
    ),
    (
        "Read",
        "Exact content for something already identified by a search or an outline.",
        [
            "cograph_read_node",
            "cograph_read_file_range",
            "cograph_wiki_page",
            "cograph_collection_document",
            "cograph_read_chunk",
        ],
    ),
    (
        "Traverse",
        "Follow the code graph outwards from a node.",
        ["cograph_related"],
    ),
]

# Bounds enforced by a validator rather than by a field constraint, so they are
# invisible to both the wire schema and the argument model's own schema. Keyed by
# (tool, parameter); asserted to still exist, so renaming either fails the build
# rather than silently dropping the note.
VALIDATOR_NOTES: dict[tuple[str, str], str] = {
    ("cograph_retrieve", "top_k"): "silently clamped to 25",
}


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


def render_api(schema: dict) -> str:
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
        "# Generated by scripts/gen_reference_docs.py — do not edit by hand.",
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


def load_tools() -> list[tuple[Any, dict[str, dict]]]:
    """Every registered MCP tool, paired with its argument model's JSON schema.

    Two schemas per tool, and both are needed. `tool.inputSchema` is what an agent
    actually receives — FastMCP derives it from the handler's signature, so it
    carries names, types and defaults but *no* bounds. The bounds live on the
    Pydantic model the handler validates against, which is where a call is
    rejected. Merging the two is the only way the table can state both what the
    wire contract looks like and what will actually be accepted.
    """
    os.environ.setdefault("COGRAPH_ENVIRONMENT", "development")
    sys.path.insert(0, str(REPO_ROOT))
    from pydantic import BaseModel  # noqa: PLC0415

    from backend.app.mcp.server import (  # noqa: PLC0415
        build_mcp_services,
        create_mcp_server,
    )

    services, _ = build_mcp_services()
    server = create_mcp_server(services=services)
    tools = asyncio.run(server.list_tools())

    out = []
    for tool in sorted(tools, key=lambda t: t.name):
        module = importlib.import_module(
            server._tool_manager.get_tool(tool.name).fn.__module__
        )
        wire = set(tool.inputSchema.get("properties") or {})
        # Exact field-set match, never a best guess: a near-match would quietly
        # attach the wrong tool's bounds, which is worse than having none.
        models = [
            obj
            for obj in vars(module).values()
            if isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj is not BaseModel
            and set(obj.model_fields) == wire
        ]
        if len(models) != 1:
            raise SystemExit(
                f"{tool.name}: expected exactly one argument model in "
                f"{module.__name__} whose fields are {sorted(wire)}, found "
                f"{len(models)}. The handler's signature and its validation model "
                "have diverged — reconcile them, or this reference silently loses "
                "every bound for this tool."
            )
        out.append((tool, (models[0].model_json_schema().get("properties") or {})))
    return out


def json_type(node: dict, defs: dict) -> str:
    """Render a property's type, resolving `$ref`s so enums show their values.

    An enum is rendered as its members rather than as "string": the member list is
    the only part an agent constructing a call actually needs, and a `$ref` to a
    `$defs` entry it cannot see is useless to a human reader.
    """
    if ref := node.get("$ref"):
        return json_type(defs.get(ref.rsplit("/", 1)[-1]) or {}, defs)
    if variants := (node.get("anyOf") or node.get("oneOf")):
        # `T | None` is how an omittable parameter is encoded; the Required and
        # Default columns already say that, so drop the null arm.
        real = [v for v in variants if v.get("type") != "null"]
        return " or ".join(json_type(v, defs) for v in real) or "any"
    if enum := node.get("enum"):
        # Escaped pipe: this lands inside a markdown table cell.
        return " \\| ".join(f"`{v}`" for v in enum)
    if node.get("type") == "array":
        inner = json_type(node.get("items") or {}, defs)
        # Parenthesise a rendered enum, or `a | b[]` reads as "a, or a list of b".
        return f"({inner})[]" if "\\|" in inner else f"{inner}[]"
    if node.get("type") == "string" and (fmt := node.get("format")):
        return fmt
    return node.get("type") or "any"


def bounds(node: dict, tool_name: str, param: str) -> str:
    """Human-readable constraint list for one parameter."""
    parts = []
    lo, hi = node.get("minimum"), node.get("maximum")
    if lo is not None and hi is not None:
        parts.append(f"{lo}–{hi}")
    elif lo is not None:
        parts.append(f"≥ {lo}")
    elif hi is not None:
        parts.append(f"≤ {hi}")
    if node.get("minLength"):
        parts.append("non-empty")
    if (max_len := node.get("maxLength")) is not None:
        parts.append(f"≤ {max_len} chars")
    if note := VALIDATOR_NOTES.get((tool_name, param)):
        parts.append(note)
    return ", ".join(parts) or "—"


def render_mcp(tools: list[tuple[Any, dict[str, dict]]]) -> str:
    for tool_name, param in VALIDATOR_NOTES:
        if not any(
            t.name == tool_name and param in (t.inputSchema.get("properties") or {})
            for t, _ in tools
        ):
            raise SystemExit(
                f"VALIDATOR_NOTES references {tool_name}.{param}, which no longer "
                "exists. Delete the note or point it at the new name."
            )

    by_name = {tool.name: (tool, model_props) for tool, model_props in tools}
    grouped = {name for _, _, names in TOOL_GROUPS for name in names}
    ungrouped = sorted(set(by_name) - grouped)

    lines = [
        "---",
        "# Generated by scripts/gen_reference_docs.py — do not edit by hand.",
        "editLink: false",
        "---",
        "",
        "# MCP tool reference",
        "",
        f"The exact signature of every tool an agent sees: **{len(tools)} tools**, "
        "generated from the MCP server's own `list_tools()` response, so it cannot "
        "drift from the code. Each tool's text is reproduced verbatim — it is what "
        "the agent reads when deciding which tool to call.",
        "",
        "See [MCP server](/mcp) for connecting a client, the response envelope, the "
        "token budget and the built-in playbook.",
        "",
        "::: info Where the bounds come from",
        "The schema an agent receives is derived from the handler signature, so it "
        "carries types and defaults but **no** bounds. The bounds column comes from "
        "the argument model the server validates against: exceeding one is a "
        "validation error, not a silent truncation — with the single exception noted "
        "on `cograph_retrieve`.",
        ":::",
        "",
    ]

    for title, blurb, names in [
        *TOOL_GROUPS,
        *([("Other", "", ungrouped)] if ungrouped else []),
    ]:
        lines += [f"## {title}", ""]
        if blurb:
            lines += [blurb, ""]
        for name in names:
            tool, model_props = by_name[name]
            schema = tool.inputSchema
            defs = schema.get("$defs") or {}
            required = set(schema.get("required") or [])

            lines += [f"### `{name}`", ""]
            # One paragraph per line: the description is a one-line summary
            # followed by "Use when:" and "Do NOT use", and markdown would
            # otherwise collapse all three into a single run-on paragraph.
            for line in (tool.description or "").split("\n"):
                if text := line.strip():
                    lines += [text, ""]

            props = schema.get("properties") or {}
            if not props:
                lines += ["Takes no parameters.", ""]
                continue

            lines += [
                "| Parameter | Type | Required | Default | Bounds |",
                "| --- | --- | --- | --- | --- |",
            ]
            for param, node in props.items():
                default = node.get("default")
                shown = (
                    "—"
                    if default is None
                    else f"`{str(default).lower() if isinstance(default, bool) else default}`"
                )
                lines.append(
                    f"| `{param}` | {json_type(node, defs)} "
                    f"| {'yes' if param in required else 'no'} | {shown} "
                    f"| {bounds(model_props.get(param) or {}, name, param)} |"
                )
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if either page is out of date",
    )
    args = parser.parse_args()

    pages = [
        (API_OUTPUT, render_api(load_schema()), "The REST API changed"),
        (MCP_OUTPUT, render_mcp(load_tools()), "The MCP tool surface changed"),
    ]

    if args.check:
        stale = [
            (path, reason)
            for path, rendered, reason in pages
            if (path.read_text(encoding="utf-8") if path.exists() else "") != rendered
        ]
        for path, reason in stale:
            print(
                f"{path.relative_to(REPO_ROOT)} is out of date. {reason} without "
                "regenerating the reference. Run:\n"
                "    python scripts/gen_reference_docs.py\n"
                "and commit the result.",
                file=sys.stderr,
            )
        if stale:
            return 1
        for path, _, _ in pages:
            print(f"{path.relative_to(REPO_ROOT)} is up to date.")
        return 0

    for path, rendered, _ in pages:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
