#!/usr/bin/env python3
"""Print the focused merge quality gate bundle for wiki/API task types."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Literal


GateLevel = Literal["mandatory", "conditional"]


@dataclass(frozen=True, slots=True)
class Gate:
    level: GateLevel
    command: str
    why: str


GATE_BUNDLES: dict[str, tuple[Gate, ...]] = {
    "wiki-llm": (
        Gate(
            "mandatory",
            "python -m ruff check backend/app/wiki backend/tests/unit/wiki",
            "Covers the LLM-driven wiki pipeline (context/prompts/retrieval/citations/store/queries) and its tests.",
        ),
        Gate(
            "mandatory",
            "python -m pytest backend/tests/unit/wiki -q",
            "Exercises the new wiki pipeline end-to-end (Stages 1-6) plus citation resolver, store, and read facade.",
        ),
        Gate(
            "conditional",
            "python -m backend.app.cli wiki dry-run --repository-id <uuid> --stages 1-5",
            "Run on a real repo when prompts, retrieval bundling, or page-writer behavior changed.",
        ),
        Gate(
            "conditional",
            "python -m backend.app.cli wiki run --repository-id <uuid> --persist",
            "Persist a regenerated wiki to a non-prod DB to inspect documents-table output before merge.",
        ),
    ),
    "api-mcp": (
        Gate(
            "mandatory",
            "python -m ruff check backend/app/api backend/app/mcp backend/app/rag "
            "backend/tests/test_retrieval_api.py",
            "Covers REST/MCP/RAG contract code and the focused API/MCP tests.",
        ),
        Gate(
            "mandatory",
            "python -m pytest backend/tests/test_retrieval_api.py -q",
            "Proves response shape, citations, and ready-state behavior on the retrieval API.",
        ),
        Gate(
            "conditional",
            "python -m pytest backend/tests/test_mcp_transport.py -q",
            "Run when mounted or standalone MCP transport wiring changes.",
        ),
    ),
    "frontend-wiki": (
        Gate(
            "mandatory",
            "cd web && npm run typecheck",
            "Validates frontend TypeScript contracts.",
        ),
        Gate(
            "mandatory",
            "cd web && npm run lint",
            "Runs Biome on the frontend source.",
        ),
        Gate(
            "mandatory",
            "cd web && npm run test -- RepoWikiPage",
            "Exercises the wiki page route/component regression suite.",
        ),
        Gate(
            "conditional",
            "cd web && npm run build",
            "Run when routing, bundle shape, or production-only rendering could be affected.",
        ),
    ),
    "deployment-config": (
        Gate(
            "mandatory",
            "docker compose config",
            "Validates Compose syntax and resolved environment defaults.",
        ),
        Gate(
            "mandatory",
            "helm lint helm/cograph",
            "Validates chart metadata and templates.",
        ),
        Gate(
            "mandatory",
            "helm template cograph helm/cograph >/tmp/cograph-helm.yaml",
            "Renders Kubernetes manifests for review and schema drift checks.",
        ),
        Gate(
            "conditional",
            "docker compose build backend worker web",
            "Run when Dockerfiles, runtime dependencies, or packaged web/backend paths changed.",
        ),
        Gate(
            "conditional",
            "python -m pytest backend/tests/test_app.py backend/tests/test_mcp_transport.py -q",
            "Run when app mounting, `/mcp`, health, or packaged proxy assumptions changed.",
        ),
    ),
}


def render_json(kinds: list[str]) -> str:
    return json.dumps(
        {
            kind: [asdict(gate) for gate in GATE_BUNDLES[kind]]
            for kind in _resolve_kinds(kinds)
        },
        indent=2,
    )


def render_markdown(kinds: list[str]) -> str:
    lines: list[str] = ["# Cograph merge quality gates", ""]
    for kind in _resolve_kinds(kinds):
        lines.extend([f"## {kind}", ""])
        for gate in GATE_BUNDLES[kind]:
            lines.extend(
                [
                    f"- **{gate.level}**: `{gate.command}`",
                    f"  - {gate.why}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _resolve_kinds(kinds: list[str]) -> list[str]:
    return kinds or list(GATE_BUNDLES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind",
        nargs="*",
        choices=tuple(GATE_BUNDLES),
        help="Gate kind(s) to print. Omit to print all.",
    )
    parser.add_argument("--list", action="store_true", help="List gate kinds only.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format.",
    )
    args = parser.parse_args()

    if args.list:
        print("\n".join(GATE_BUNDLES))
        return 0
    if args.format == "json":
        print(render_json(args.kind))
    else:
        print(render_markdown(args.kind), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
