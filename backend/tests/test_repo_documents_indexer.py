from __future__ import annotations


from sqlalchemy import select

from backend.app.graph.ingest import GraphIngestService
from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, SyncSchedule
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repo_document_chunk_mention import RepoDocumentChunkMention
from backend.app.models.repository import Repository
from backend.app.repo_docs.discover import RepoDocumentKind
from backend.app.repo_docs.chunker import RepoDocumentChunkDraft
from backend.app.repo_docs.indexer import RepoDocumentIndexer
from backend.app.repo_docs.symbol_linker import LinkedMention, RepoDocumentSymbolLinker


async def test_repo_document_indexer_indexes_markdown_and_resolves_mentions(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    checkout_path.mkdir()
    (checkout_path / "service.py").write_text(
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (checkout_path / "README.md").write_text(
        "# Demo\n\nUse `helper` to compute values.\n",
        encoding="utf-8",
    )
    await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )

    result = await RepoDocumentIndexer().index_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    documents = list(
        (
            await db_session.scalars(
                select(RepoDocument).where(RepoDocument.repository_id == repository.id)
            )
        ).all()
    )
    chunks = list((await db_session.scalars(select(RepoDocumentChunk))).all())
    code_nodes = {
        node.name: node
        for node in (
            await db_session.scalars(
                select(CodeNode).where(CodeNode.repository_id == repository.id)
            )
        ).all()
    }

    assert result.discovered_files == 1
    assert result.indexed_documents == 1
    assert result.indexed_chunks == 1
    assert result.unchanged_documents == 0
    assert result.deleted_documents == 0
    assert result.replaced_files == ("README.md",)
    assert len(documents) == 1
    assert documents[0].title == "Demo"
    assert len(chunks) == 1
    assert chunks[0].mentions == [str(code_nodes["helper"].id)]
    # Regression for C5: the normalized join table must be populated alongside
    # the legacy `mentions` array so 0008_finalize can drop the array.
    mentions = list(
        (
            await db_session.scalars(
                select(RepoDocumentChunkMention).where(
                    RepoDocumentChunkMention.chunk_id == chunks[0].id
                )
            )
        ).all()
    )
    assert {m.code_node_id for m in mentions} == {code_nodes["helper"].id}


async def test_repo_document_indexer_reindexes_only_changed_documents_and_prunes_deleted_ones(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    docs_path = checkout_path / "docs"
    docs_path.mkdir(parents=True)
    (checkout_path / "service.py").write_text(
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    first_doc = checkout_path / "README.md"
    second_doc = docs_path / "guide.md"
    first_doc.write_text("# Demo\n\nUse `helper`.\n", encoding="utf-8")
    second_doc.write_text("# Guide\n\nStable content.\n", encoding="utf-8")
    await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )

    indexer = RepoDocumentIndexer()
    await indexer.index_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    first_doc.write_text("# Demo\n\nUse `helper` carefully.\n", encoding="utf-8")
    second_doc.unlink()

    result = await indexer.index_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    documents = list(
        (
            await db_session.scalars(
                select(RepoDocument)
                .where(RepoDocument.repository_id == repository.id)
                .order_by(RepoDocument.file_path.asc())
            )
        ).all()
    )

    assert result.discovered_files == 1
    assert result.indexed_documents == 1
    assert result.unchanged_documents == 0
    assert result.deleted_documents == 1
    assert result.replaced_files == ("README.md",)
    assert [document.file_path for document in documents] == ["README.md"]


async def test_build_chunks_deduplicates_mentions_resolving_to_same_code_node(
    db_session,
    tmp_path,
):
    # Regression: two backtick symbols in one chunk (e.g. `click.argument` and
    # `argument`) can both resolve via symbol_linker to the same CodeNode.
    # Without the seen_node_ids guard this caused an IntegrityError on the
    # composite PK (chunk_id, code_node_id) of pk_repo_document_chunk_mentions.
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    # Create a real CodeNode so FK on repo_document_chunk_mentions is satisfied.
    code_node = CodeNode(
        repository_id=repository.id,
        file_path="cli.py",
        qualified_name="cli.argument",
        node_type=CodeNodeType.FUNCTION,
        name="argument",
        language="python",
        start_line=1,
        end_line=3,
        content="def argument(): pass",
        content_hash="deadbeef" * 8,
    )
    db_session.add(code_node)
    await db_session.flush()

    document = RepoDocument(
        repository_id=repository.id,
        file_path="docs/guide.md",
        title="Guide",
        content="Use `click.argument` or `argument`.",
        content_hash="abc",
        bytes=36,
    )
    db_session.add(document)
    await db_session.flush()

    class _DuplicateLinker:
        """Returns the same node_id for both raw symbols in the chunk."""

        async def link_chunk_mentions(
            self, *, session, repository_id, document_file_path, chunk_content
        ):
            return [
                LinkedMention(
                    node_id=code_node.id, name="argument", file_path="cli.py"
                ),
                LinkedMention(
                    node_id=code_node.id, name="argument", file_path="cli.py"
                ),
            ]

    indexer = RepoDocumentIndexer(symbol_linker=_DuplicateLinker())
    chunk_draft = RepoDocumentChunkDraft(
        chunk_index=0,
        heading_path=[],
        content="Use `click.argument` or `argument`.",
    )

    # Must not raise IntegrityError
    await indexer._build_chunks(
        session=db_session,
        repository_id=repository.id,
        document=document,
        document_kind=RepoDocumentKind.REPO_DOC,
        chunk_drafts=[chunk_draft],
    )
    await db_session.flush()

    mention_rows = list(
        (await db_session.scalars(select(RepoDocumentChunkMention))).all()
    )
    # Both raw symbols resolved to the same node — only ONE row must exist.
    assert len(mention_rows) == 1
    assert mention_rows[0].code_node_id == code_node.id


async def test_repo_document_indexer_discovers_auxiliary_evidence_and_tags_chunks(
    db_session,
    tmp_path,
):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    checkout_path = tmp_path / "checkout"
    (checkout_path / "docs").mkdir(parents=True)
    (checkout_path / "examples").mkdir(parents=True)
    (checkout_path / "tests").mkdir(parents=True)
    (checkout_path / ".github" / "workflows").mkdir(parents=True)
    (checkout_path / ".codexpotter" / "kb").mkdir(parents=True)
    (checkout_path / "web" / "node_modules" / "pkg").mkdir(parents=True)
    (checkout_path / "service.py").write_text(
        "def helper() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (checkout_path / "README.md").write_text(
        "# Demo\n\nUse `helper`.\n", encoding="utf-8"
    )
    (checkout_path / "docs" / "guide.md").write_text(
        "# Guide\n\nRead `helper`.\n", encoding="utf-8"
    )
    (checkout_path / "examples" / "demo.py").write_text(
        "from service import helper\n", encoding="utf-8"
    )
    (checkout_path / "tests" / "test_helper.py").write_text(
        "from service import helper\n", encoding="utf-8"
    )
    (checkout_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\n", encoding="utf-8"
    )
    (checkout_path / ".codexpotter" / "kb" / "guide.md").write_text(
        "# Scratch\n", encoding="utf-8"
    )
    (checkout_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n", encoding="utf-8"
    )
    (checkout_path / "web" / "node_modules" / "pkg" / "package.json").write_text(
        '{ "name": "pkg" }\n',
        encoding="utf-8",
    )

    await GraphIngestService().ingest_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )

    result = await RepoDocumentIndexer().index_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout_path,
    )
    await db_session.commit()

    documents = {
        document.file_path: document
        for document in (
            await db_session.scalars(
                select(RepoDocument).where(RepoDocument.repository_id == repository.id)
            )
        ).all()
    }
    chunks = {
        document.file_path: list(
            (
                await db_session.scalars(
                    select(RepoDocumentChunk)
                    .where(RepoDocumentChunk.document_id == document.id)
                    .order_by(RepoDocumentChunk.chunk_index.asc())
                )
            ).all()
        )
        for document in documents.values()
    }

    assert result.discovered_files == 6
    assert set(documents) == {
        ".github/workflows/ci.yml",
        "README.md",
        "docs/guide.md",
        "examples/demo.py",
        "pyproject.toml",
        "tests/test_helper.py",
    }
    assert ".codexpotter/kb/guide.md" not in documents
    assert "web/node_modules/pkg/package.json" not in documents
    assert documents["examples/demo.py"].title == "Example: demo.py"
    assert documents["pyproject.toml"].title == "Config: pyproject.toml"
    assert documents[".github/workflows/ci.yml"].title == "Workflow: ci.yml"
    assert documents["tests/test_helper.py"].title == "Test: test_helper.py"
    assert chunks["examples/demo.py"][0].heading_path == [
        RepoDocumentKind.EXAMPLE.value
    ]
    assert chunks["pyproject.toml"][0].heading_path == [RepoDocumentKind.CONFIG.value]
    assert chunks[".github/workflows/ci.yml"][0].heading_path == [
        RepoDocumentKind.WORKFLOW.value
    ]
    assert chunks["tests/test_helper.py"][0].heading_path == [
        RepoDocumentKind.TEST.value
    ]


async def test_symbol_linker_resolves_unbackticked_qualified_names(db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/go-types.git",
        name="go-types",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    country_alpha2 = CodeNode(
        repository_id=repository.id,
        file_path="country/alpha2.go",
        qualified_name="country.Alpha2",
        node_type=CodeNodeType.FUNCTION,
        name="Alpha2",
        language="go",
        start_line=1,
        end_line=20,
        content='func Alpha2() string { return "US" }',
        signature="func Alpha2() string",
        node_metadata={"package_qualified_name": "country"},
        content_hash="a" * 64,
    )
    language_alpha2 = CodeNode(
        repository_id=repository.id,
        file_path="language/alpha2.go",
        qualified_name="language.Alpha2",
        node_type=CodeNodeType.FUNCTION,
        name="Alpha2",
        language="go",
        start_line=1,
        end_line=20,
        content='func Alpha2() string { return "en" }',
        signature="func Alpha2() string",
        node_metadata={"package_qualified_name": "language"},
        content_hash="b" * 64,
    )
    db_session.add_all([country_alpha2, language_alpha2])
    await db_session.commit()

    mentions = await RepoDocumentSymbolLinker().link_chunk_mentions(
        session=db_session,
        repository_id=repository.id,
        document_file_path="docs/country.md",
        chunk_content="Use country.Alpha2 when you need the country code.",
    )

    assert [mention.node_id for mention in mentions] == [country_alpha2.id]


async def test_symbol_linker_uses_path_proximity_for_plain_function_calls(db_session):
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/go-types.git",
        name="go-types",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    country_parse = CodeNode(
        repository_id=repository.id,
        file_path="country/parse.go",
        qualified_name="country.Parse",
        node_type=CodeNodeType.FUNCTION,
        name="Parse",
        language="go",
        start_line=1,
        end_line=20,
        content="func Parse() {}",
        signature="func Parse()",
        node_metadata={"package_qualified_name": "country"},
        content_hash="c" * 64,
    )
    language_parse = CodeNode(
        repository_id=repository.id,
        file_path="language/parse.go",
        qualified_name="language.Parse",
        node_type=CodeNodeType.FUNCTION,
        name="Parse",
        language="go",
        start_line=1,
        end_line=20,
        content="func Parse() {}",
        signature="func Parse()",
        node_metadata={"package_qualified_name": "language"},
        content_hash="d" * 64,
    )
    db_session.add_all([country_parse, language_parse])
    await db_session.commit()

    mentions = await RepoDocumentSymbolLinker().link_chunk_mentions(
        session=db_session,
        repository_id=repository.id,
        document_file_path="docs/language/guide.md",
        chunk_content="Parse() is the main entry point for this package.",
    )

    assert [mention.node_id for mention in mentions] == [language_parse.id]
