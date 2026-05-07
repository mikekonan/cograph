from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from backend.app.config import Settings, get_settings
from backend.app.core.auth import hash_password
from backend.app.db.session import SessionManager
from backend.app.models.enums import UserRole
from backend.app.models.user import User


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cograph-backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin = subparsers.add_parser("create-admin")
    create_admin.add_argument("--email", required=True)
    create_admin.add_argument("--password", default=None)
    create_admin.add_argument("--name", default="Admin")

    reset_password = subparsers.add_parser("reset-password")
    reset_password.add_argument("--email", required=True)
    reset_password.add_argument("--password", default=None)

    wiki = subparsers.add_parser("wiki", help="LLM-driven wiki generation")
    wiki_sub = wiki.add_subparsers(dest="wiki_command", required=True)

    wiki_dry_run = wiki_sub.add_parser(
        "dry-run",
        help="Run stages 1-5 of the LLM-driven wiki pipeline without persisting.",
    )
    wiki_dry_run.add_argument("--repository-id", required=True)
    wiki_dry_run.add_argument(
        "--stages",
        default="1,2,3",
        help=(
            "Comma-separated stage numbers (e.g. '1,2,3' or '1-5'). "
            "Stages 1-3 print JSON; stage 4 also writes one .md per page; "
            "stage 5 also resolves citations and emits resolved markdown."
        ),
    )
    wiki_dry_run.add_argument(
        "--source-commit",
        default=None,
        help="Override commit SHA; falls back to repositories.last_commit.",
    )
    wiki_dry_run.add_argument(
        "--output-dir",
        default=None,
        help=(
            "When stage 4+ is included, write each page as <slug>.md under "
            "this directory. Defaults to a fresh temp dir under $TMPDIR."
        ),
    )

    wiki_run = wiki_sub.add_parser(
        "run",
        help="Run the full LLM-driven wiki pipeline and persist into `documents`.",
    )
    wiki_run.add_argument("--repository-id", required=True)
    wiki_run.add_argument(
        "--source-commit",
        default=None,
        help="Override commit SHA; falls back to repositories.last_commit.",
    )
    wiki_run.add_argument(
        "--persist",
        action="store_true",
        help="Required: persist results into the `documents` table.",
    )

    md_rag = subparsers.add_parser("md-rag")
    md_rag.add_argument(
        "--collection-id",
        help="Target specific collection; if omitted, all collections are processed",
    )
    md_rag.add_argument(
        "--action",
        choices=("embed", "resolve-links", "all"),
        default="all",
        help="embed: backfill embeddings; resolve-links: resolve cross-document links; all: both",
    )

    reencrypt = subparsers.add_parser(
        "reencrypt-secrets",
        help=(
            "CRIT-03 phase 2: re-encrypt llm_secrets + identity_providers "
            "rows from the legacy JWT-derived key to the independent "
            "auth.*_encryption_secret keys. Idempotent."
        ),
    )
    reencrypt.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the migration plan without committing.",
    )

    return parser


def resolve_password(raw_password: str | None) -> str:
    if raw_password == "-":
        return sys.stdin.read().strip()
    if raw_password:
        return raw_password

    env_password = os.environ.get("COGRAPH_ADMIN_PASSWORD")
    if env_password:
        return env_password

    raise ValueError(
        "Password must be provided via --password, stdin, or COGRAPH_ADMIN_PASSWORD"
    )


async def create_admin(
    *,
    settings: Settings,
    email: str,
    password: str,
    name: str,
) -> int:
    session_manager = SessionManager(settings)
    try:
        async with session_manager.session() as session:
            existing_admin = await session.scalar(
                select(User).where(User.role == UserRole.ADMIN)
            )
            if existing_admin is not None:
                print(existing_admin.email)
                return 0

            user = User(
                email=email,
                password_hash=hash_password(password),
                name=name,
                role=UserRole.ADMIN,
            )
            session.add(user)
            await session.commit()
            print(user.email)
            return 0
    finally:
        await session_manager.dispose()


async def reset_password(
    *,
    settings: Settings,
    email: str,
    password: str,
) -> int:
    session_manager = SessionManager(settings)
    try:
        async with session_manager.session() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                print(f"User not found: {email}", file=sys.stderr)
                return 1

            user.password_hash = hash_password(password)
            await session.commit()
            print(user.email)
            return 0
    finally:
        await session_manager.dispose()


def _parse_stages(raw: str) -> set[str]:
    """Parse '1,2,3' or '1-4' into a set of string-stage numbers."""
    parsed: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo_str, hi_str = token.split("-", 1)
            lo, hi = int(lo_str), int(hi_str)
            for n in range(lo, hi + 1):
                parsed.add(str(n))
        else:
            parsed.add(token)
    return parsed


async def run_wiki_dry_run(
    *,
    settings: Settings,
    repository_id: str,
    stages: str,
    source_commit_override: str | None,
    output_dir: str | None = None,
) -> int:
    """`cograph wiki dry-run` — runs Stages 1-5 of the LLM-driven wiki pipeline.

    Resolves an `OpenAICompatibleStructuredProvider` from settings.completion
    when a real key is configured. Falls back to a `FakeStructuredProvider`
    with deterministic canned JSON when `COGRAPH_FAKE_LLM=1`. When stage 4 is
    requested, also wires a `WikiRetrievalService` (FakeEmbedProvider in the
    fake-LLM path; settings-based otherwise).
    """
    parsed_stages = _parse_stages(stages)
    unsupported = parsed_stages - {"1", "2", "3", "4", "5"}
    if unsupported:
        raise ValueError(
            f"Stages {sorted(unsupported)} are not supported by `wiki dry-run`. "
            "Use `wiki run --persist` for stage 6."
        )
    include_stage_5 = "5" in parsed_stages
    include_stage_4 = include_stage_5 or "4" in parsed_stages

    from backend.app.models.repository import Repository
    from backend.app.wiki.llm_client import (
        FakeStructuredProvider,
        OpenAICompatibleStructuredProvider,
        StructuredCompletionProvider,
    )
    from backend.app.wiki.pipeline import (
        WikiGenerationConfig,
        run_stages_1_to_3,
        run_stages_1_to_4,
        run_stages_1_to_5,
    )

    repository_uuid = UUID(repository_id)
    session_manager = SessionManager(settings)

    llm: StructuredCompletionProvider
    fake_mode = os.environ.get("COGRAPH_FAKE_LLM") == "1"
    if fake_mode:
        from backend.app.wiki.schemas import MindMap, PagePlan, RepoOverview

        fake = FakeStructuredProvider()
        fake.queue(
            RepoOverview(
                one_line="Fake overview for dry-run",
                long_description=(
                    "This is a deterministic fake overview returned when "
                    "COGRAPH_FAKE_LLM=1; it lets the dry-run CLI verify wiring "
                    "without burning real tokens."
                ),
            ).model_dump_json()
        )
        # Stage 1.5 — empty mindmap is fine for fake-mode wiring checks.
        fake.queue(MindMap().model_dump_json())
        fake_plan = PagePlan.model_validate(
            {
                "pages": [
                    {"slug": "index", "title": "Overview", "purpose": "Landing"},
                    {
                        "slug": "architecture",
                        "title": "Architecture",
                        "purpose": "Design",
                    },
                    {
                        "slug": "getting-started",
                        "title": "Getting started",
                        "purpose": "Setup",
                    },
                ]
            }
        )
        fake.queue(fake_plan.model_dump_json())
        if include_stage_4:
            for page in fake_plan.pages:
                fake.queue(
                    f"# {page.title}\n\n"
                    f"_Fake body for `{page.slug}` from COGRAPH_FAKE_LLM=1._\n"
                )
        llm = fake
    else:
        api_key = settings.completion.api_key.get_secret_value()
        if not api_key:
            raise ValueError(
                "wiki dry-run needs either COGRAPH_FAKE_LLM=1 or "
                "completion.api_key set to an OpenAI-compatible key."
            )
        llm = OpenAICompatibleStructuredProvider(
            api_url=settings.completion.api_url,
            api_key=api_key,
            model=settings.completion.model,
        )

    retriever = None
    if include_stage_4:
        from backend.app.wiki.retrieval import WikiRetrievalService

        if fake_mode:
            from backend.app.llm.embedder import FakeEmbedProvider

            class _NoopHybridRetriever:
                async def retrieve(self, *_args, **_kwargs):  # noqa: ANN001, D401
                    return []

            retriever = WikiRetrievalService(
                hybrid=_NoopHybridRetriever(),  # type: ignore[arg-type]
                embedder=FakeEmbedProvider(dims=settings.embedding.dimensions),
            )
        else:
            from backend.app.rag.runtime import (
                build_hybrid_retriever,
                build_query_embed_provider,
            )

            embedder = build_query_embed_provider(settings)
            if embedder is None:
                raise ValueError(
                    "wiki dry-run --stages includes 4 but settings.embedding is "
                    "disabled; cannot run real retrieval. Set COGRAPH_FAKE_LLM=1 "
                    "or enable embeddings."
                )
            retriever = WikiRetrievalService(
                hybrid=build_hybrid_retriever(settings),
                embedder=embedder,
            )

    try:
        async with session_manager.session() as session:
            repository = await session.get(Repository, repository_uuid)
            if repository is None:
                raise ValueError(f"Repository not found: {repository_id}")
            commit = source_commit_override or repository.last_commit
            if not commit:
                raise ValueError(
                    f"Repository {repository_id} has no last_commit; "
                    "pass --source-commit to override."
                )
            cfg = WikiGenerationConfig()
            if include_stage_5:
                assert retriever is not None
                result_5 = await run_stages_1_to_5(
                    session=session,
                    repository_id=repository_uuid,
                    source_commit=commit,
                    llm=llm,
                    retriever=retriever,
                    config=cfg,
                )
                payload = _build_dry_run_payload_through_stage_5(
                    result=result_5,
                    repository_uuid=repository_uuid,
                    commit=commit,
                    model=llm.model,
                )
                if result_5.resolved:
                    out_dir = _resolve_output_dir(output_dir, repository_uuid)
                    written = _write_resolved_to_dir(out_dir, result_5.resolved)
                    payload["pages_written_to"] = str(out_dir)
                    payload["pages_written"] = written
            elif include_stage_4:
                assert retriever is not None
                result_4 = await run_stages_1_to_4(
                    session=session,
                    repository_id=repository_uuid,
                    source_commit=commit,
                    llm=llm,
                    retriever=retriever,
                    config=cfg,
                )
                payload = _build_dry_run_payload_through_stage_4(
                    result=result_4,
                    repository_uuid=repository_uuid,
                    commit=commit,
                    model=llm.model,
                )
                if result_4.drafts:
                    out_dir = _resolve_output_dir(output_dir, repository_uuid)
                    written = _write_drafts_to_dir(out_dir, result_4.drafts)
                    payload["drafts_written_to"] = str(out_dir)
                    payload["drafts_written"] = written
            else:
                result_3 = await run_stages_1_to_3(
                    session=session,
                    repository_id=repository_uuid,
                    source_commit=commit,
                    llm=llm,
                    config=cfg,
                )
                payload = _build_dry_run_payload_through_stage_3(
                    result=result_3,
                    repository_uuid=repository_uuid,
                    commit=commit,
                    model=llm.model,
                )

        import json

        print(json.dumps(payload, indent=2, default=str))
        return 0
    finally:
        await session_manager.dispose()


def _build_dry_run_payload_through_stage_3(
    *,
    result,
    repository_uuid: UUID,
    commit: str,
    model: str,
) -> dict:
    return {
        "repository_id": str(repository_uuid),
        "source_commit": commit,
        "model": model,
        "context": {
            "code_node_count": result.context.code_node_count,
            "file_tree_size": len(result.context.file_tree),
            "top_summaries_size": len(result.context.top_summaries),
            "repo_doc_index_size": len(result.context.repo_doc_index),
            "previous_run_slugs": result.context.previous_run_slugs,
            "identity_hash": result.context.identity_hash,
        },
        "overview": result.overview.model_dump(),
        "plan": {
            "pages": [page.model_dump() for page in result.plan.pages],
        },
        # Phase 29.3 T7: pairwise overlap report. Empty list on a clean
        # plan; populated when the planner shipped redundant pages.
        "plan_quality": result.plan_quality.model_dump(),
    }


def _build_dry_run_payload_through_stage_4(
    *,
    result,
    repository_uuid: UUID,
    commit: str,
    model: str,
) -> dict:
    payload = _build_dry_run_payload_through_stage_3(
        result=result,
        repository_uuid=repository_uuid,
        commit=commit,
        model=model,
    )
    payload["drafts"] = [
        {
            "slug": draft.slug,
            "title": draft.title,
            "model": draft.model,
            "body_chars": len(draft.body_md),
        }
        for draft in result.drafts
    ]
    payload["page_failures"] = result.page_failures
    return payload


def _build_dry_run_payload_through_stage_5(
    *,
    result,
    repository_uuid: UUID,
    commit: str,
    model: str,
) -> dict:
    payload = _build_dry_run_payload_through_stage_4(
        result=result,
        repository_uuid=repository_uuid,
        commit=commit,
        model=model,
    )
    payload["resolved"] = [
        {
            "slug": page.slug,
            "title": page.title,
            "sort_order": page.sort_order,
            "content_chars": len(page.content),
            "citations": [c.model_dump() for c in page.citations],
            "source_node_ids": [str(nid) for nid in page.source_node_ids],
            "source_repo_doc_chunk_ids": [
                str(cid) for cid in page.source_repo_doc_chunk_ids
            ],
            "unresolved_placeholders": page.unresolved_placeholders,
        }
        for page in result.resolved
    ]
    payload["unresolved_placeholders_total"] = sum(
        len(page.unresolved_placeholders) for page in result.resolved
    )
    return payload


def _write_resolved_to_dir(out_dir, resolved) -> list[str]:
    paths: list[str] = []
    for page in resolved:
        target = out_dir / f"{page.slug}.md"
        target.write_text(page.content, encoding="utf-8")
        paths.append(str(target))
    return paths


async def run_wiki_persist(
    *,
    settings: Settings,
    repository_id: str,
    source_commit_override: str | None,
) -> int:
    """`cograph wiki run --persist` — runs all 5 stages and writes to `documents`."""
    from backend.app.models.repository import Repository
    from backend.app.wiki.llm_client import (
        FakeStructuredProvider,
        OpenAICompatibleStructuredProvider,
        StructuredCompletionProvider,
    )
    from backend.app.wiki.pipeline import (
        WikiGenerationConfig,
        run_wiki_generation,
    )
    from backend.app.wiki.retrieval import WikiRetrievalService

    repository_uuid = UUID(repository_id)
    session_manager = SessionManager(settings)

    llm: StructuredCompletionProvider
    fake_mode = os.environ.get("COGRAPH_FAKE_LLM") == "1"
    if fake_mode:
        from backend.app.wiki.schemas import PagePlan, RepoOverview

        fake = FakeStructuredProvider()
        fake.queue(
            RepoOverview(
                one_line="Fake overview for wiki run",
                long_description="Deterministic fake overview from COGRAPH_FAKE_LLM=1.",
            ).model_dump_json()
        )
        fake_plan = PagePlan.model_validate(
            {
                "pages": [
                    {"slug": "index", "title": "Overview", "purpose": "Landing"},
                    {
                        "slug": "architecture",
                        "title": "Architecture",
                        "purpose": "Design",
                    },
                    {
                        "slug": "getting-started",
                        "title": "Getting started",
                        "purpose": "Setup",
                    },
                ]
            }
        )
        fake.queue(fake_plan.model_dump_json())
        for page in fake_plan.pages:
            fake.queue(f"# {page.title}\n\nFake body for `{page.slug}`.\n")
        llm = fake
    else:
        api_key = settings.completion.api_key.get_secret_value()
        if not api_key:
            raise ValueError(
                "wiki run needs either COGRAPH_FAKE_LLM=1 or completion.api_key set."
            )
        llm = OpenAICompatibleStructuredProvider(
            api_url=settings.completion.api_url,
            api_key=api_key,
            model=settings.completion.model,
        )

    if fake_mode:
        from backend.app.llm.embedder import FakeEmbedProvider

        class _NoopHybridRetriever:
            async def retrieve(self, *_args, **_kwargs):  # noqa: ANN001, D401
                return []

        retriever = WikiRetrievalService(
            hybrid=_NoopHybridRetriever(),  # type: ignore[arg-type]
            embedder=FakeEmbedProvider(dims=settings.embedding.dimensions),
        )
    else:
        from backend.app.rag.runtime import (
            build_hybrid_retriever,
            build_query_embed_provider,
        )

        embedder = build_query_embed_provider(settings)
        if embedder is None:
            raise ValueError(
                "wiki run requires settings.embedding to be enabled. "
                "Set COGRAPH_FAKE_LLM=1 to run in fake mode."
            )
        retriever = WikiRetrievalService(
            hybrid=build_hybrid_retriever(settings),
            embedder=embedder,
        )

    try:
        async with session_manager.session() as session:
            repository = await session.get(Repository, repository_uuid)
            if repository is None:
                raise ValueError(f"Repository not found: {repository_id}")
            commit = source_commit_override or repository.last_commit
            if not commit:
                raise ValueError(
                    f"Repository {repository_id} has no last_commit; "
                    "pass --source-commit to override."
                )
            result = await run_wiki_generation(
                session=session,
                repository_id=repository_uuid,
                source_commit=commit,
                sync_run_id=None,
                llm=llm,
                retriever=retriever,
                config=WikiGenerationConfig(persist=True),
            )
            print(result.model_dump_json(indent=2))
            return 0
    finally:
        await session_manager.dispose()


def _resolve_output_dir(output_dir: str | None, repository_uuid: UUID):
    import tempfile
    from pathlib import Path

    if output_dir:
        path = Path(output_dir)
    else:
        path = Path(tempfile.mkdtemp(prefix=f"cograph-wiki-{repository_uuid}-"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_drafts_to_dir(out_dir, drafts) -> list[str]:
    paths: list[str] = []
    for draft in drafts:
        target = out_dir / f"{draft.slug}.md"
        target.write_text(draft.body_md, encoding="utf-8")
        paths.append(str(target))
    return paths


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolved_settings = settings or get_settings()

    if args.command == "create-admin":
        password = resolve_password(args.password)
        return await create_admin(
            settings=resolved_settings,
            email=args.email,
            password=password,
            name=args.name,
        )

    if args.command == "reset-password":
        password = resolve_password(args.password)
        return await reset_password(
            settings=resolved_settings,
            email=args.email,
            password=password,
        )

    if args.command == "wiki":
        if args.wiki_command == "dry-run":
            return await run_wiki_dry_run(
                settings=resolved_settings,
                repository_id=args.repository_id,
                stages=args.stages,
                source_commit_override=args.source_commit,
                output_dir=args.output_dir,
            )
        if args.wiki_command == "run":
            if not args.persist:
                parser.error("`wiki run` requires --persist to confirm DB writes.")
                return 2
            return await run_wiki_persist(
                settings=resolved_settings,
                repository_id=args.repository_id,
                source_commit_override=args.source_commit,
            )
        parser.error(f"Unsupported wiki subcommand: {args.wiki_command}")
        return 2

    if args.command == "md-rag":
        return await _run_md_rag_cli(
            settings=resolved_settings,
            collection_id=args.collection_id,
            action=args.action,
        )

    if args.command == "reencrypt-secrets":
        return await _run_reencrypt_secrets_cli(
            settings=resolved_settings,
            dry_run=args.dry_run,
        )

    parser.error(f"Unsupported command: {args.command}")
    return 2


async def _run_reencrypt_secrets_cli(
    *,
    settings: Settings,
    dry_run: bool,
) -> int:
    from backend.app.admin.secret_reencryption import (
        format_report,
        reencrypt_secrets,
    )

    session_manager = SessionManager(settings)
    try:
        async with session_manager.session() as session:
            report = await reencrypt_secrets(
                session,
                settings=settings,
                dry_run=dry_run,
            )
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        print(format_report(report))
        return 1 if report.has_failures else 0
    finally:
        await session_manager.dispose()


async def _run_md_rag_cli(
    settings: Settings,
    collection_id: str | None,
    action: str,
) -> int:
    from backend.app.llm.md_chunk_embedder import MdChunkEmbedderService
    from backend.app.llm.runtime_providers import build_runtime_providers
    from backend.app.md_rag.link_resolver import MdLinkResolver
    from backend.app.models.md_collection import MdCollection

    session_manager = SessionManager(settings)
    async with session_manager.session() as session:
        providers = await build_runtime_providers(
            session=session,
            settings=settings,
        )
        embedder = MdChunkEmbedderService(
            providers.embed_provider,
            batch_size=settings.embedding.batch_size,
        )
        resolver = MdLinkResolver()

        if collection_id is not None:
            collection_ids = [UUID(collection_id)]
        else:
            collections = list((await session.scalars(select(MdCollection))).all())
            collection_ids = [c.id for c in collections]

        total_embedded = 0
        total_resolved = 0
        for cid in collection_ids:
            if action in ("embed", "all"):
                result = await embedder.embed_collection(
                    session=session, collection_id=cid
                )
                total_embedded += result.embedded_nodes
                print(
                    f"Collection {cid}: embedded {result.embedded_nodes}, "
                    f"skipped {result.skipped_nodes}"
                )
            if action in ("resolve-links", "all"):
                resolved = await resolver.resolve_collection(
                    session=session, collection_id=cid
                )
                total_resolved += resolved
                print(f"Collection {cid}: resolved {resolved} links")

    print(f"Done. Total embedded: {total_embedded}, total resolved: {total_resolved}")
    await session_manager.dispose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run_cli(argv))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
