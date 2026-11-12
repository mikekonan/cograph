"""YAML files as repo documents: which files, how they are cut, what is stored."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from backend.app.models.enums import RepositoryStatus, SyncSchedule
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.repo_docs.chunker import YamlDocumentChunker, _span_path
from backend.app.repo_docs.discover import RepoDocumentKind, classify_repo_document
from backend.app.repo_docs.indexer import RepoDocumentIndexer
from backend.app.repo_docs.symbol_linker import RepoDocumentSymbolLinker


def _cut(content: str, max_chars: int) -> list[tuple[int, int, list[str]]]:
    drafts = YamlDocumentChunker(max_chars=max_chars).chunk("stock.yaml", content)
    lines = content.splitlines()
    # Every line once, in order: the chunks run back to back over 1..N.
    assert drafts[0].start_line == 1
    assert drafts[-1].end_line == len(lines)
    for before, after in zip(drafts, drafts[1:]):
        assert after.start_line == before.end_line + 1
    for draft in drafts:
        assert draft.content.split("\n") == [
            "# stock.yaml",
            *lines[draft.start_line - 1 : draft.end_line],
        ]
    return [(d.start_line, d.end_line, d.heading_path) for d in drafts]


LAYOUT = """\
# warehouse layout
bins:
  size: large
  rows: 12
shelves:
  depth: 40
  height: 200
trucks:
  count: 3
  dock: north
"""


def test_small_file_is_one_chunk_with_every_line() -> None:
    assert _cut(LAYOUT, 3000) == [(1, 10, [])]


def test_top_level_keys_cut_into_chunks_and_neighbours_merge_while_they_fit() -> None:
    assert _cut(LAYOUT, 60) == [
        (1, 4, ["bins"]),  # the comment above a key belongs to it
        (5, 7, ["shelves"]),
        (8, 10, ["trucks"]),
    ]
    assert _cut(LAYOUT, 90) == [(1, 7, ["bins … shelves"]), (8, 10, ["trucks"])]


def test_one_big_key_is_cut_by_its_next_level() -> None:
    catalog = """\
catalog:
  books:
    shelf: A
    count: 120
  maps:
    shelf: B
    count: 30
  atlases:
    shelf: C
    count: 4
"""
    assert _cut(catalog, 60) == [
        (1, 4, ["catalog", "books"]),  # the `catalog:` line stays in the first part
        (5, 7, ["catalog", "maps"]),
        (8, 10, ["catalog", "atlases"]),
    ]


def test_tail_of_a_cut_block_joins_the_next_block() -> None:
    racks = """\
shelf:
  aisle: north wing of the main warehouse
  bay: second row from the loading dock
  level: top
bin:
  size: large
"""
    assert _cut(racks, 100) == [
        (1, 3, ["shelf", "aisle … bay"]),
        (4, 6, ["shelf … bin"]),  # not `level: top` on its own
    ]


@pytest.mark.parametrize(
    ("first", "last", "path"),
    [
        (["W"], ["X", "x8"], ["W … X"]),
        (["X", "x9"], ["Z"], ["X … Z"]),
        (["X", "props", "a"], ["X", "props", "m"], ["X", "props", "a … m"]),
        (["doc 1", "b"], ["doc 2"], ["doc 1 … doc 2"]),
        (["X", "a"], ["X", "a"], ["X", "a"]),
    ],
)
def test_chunk_of_several_atoms_is_named_by_where_they_part(first, last, path) -> None:
    assert _span_path(first, last) == path


def test_unindented_list_belongs_to_the_key_above_it() -> None:
    packing = """\
name: packing
steps:
- id: pick
  run: take the parcel from the shelf
- id: pack
  run: put the parcel into a box
- id: label
  run: print the label for the parcel
"""
    assert _cut(packing, 90) == [
        (1, 4, ["name … steps"]),  # `name:` alone would be a one-line chunk
        (5, 6, ["steps", "pack"]),
        (7, 8, ["steps", "label"]),
    ]


def test_top_level_list_items_are_named_by_id_or_name_or_index() -> None:
    docks = """\
- id: north
  dock: 1
  city: harbour
- name: south
  dock: 2
  city: hills
- dock: 3
  city: east
  note: no name here
"""
    assert _cut(docks, 50) == [
        (1, 3, ["north"]),
        (4, 6, ["south"]),
        (7, 9, ["[2]"]),
    ]


def test_documents_split_on_separator_lines() -> None:
    routes = """\
# delivery routes
---
route: north
stops: 4
---
route: south
stops: 7
"""
    assert _cut(routes, 50) == [(1, 4, ["doc 1"]), (5, 7, ["doc 2"])]


def test_template_lines_at_zero_indent_do_not_break_the_cut() -> None:
    chart = """\
{{- if .Values.shelves }}
shelves:
  count: {{ .Values.shelves.count }}
  depth: 40
{{- end }}
bins:
  size: large
  rows: 12
"""
    assert _cut(chart, 90) == [(1, 4, ["shelves"]), (5, 8, ["bins"])]


def test_text_with_no_structure_is_cut_into_line_windows() -> None:
    notes = "notes: |\n" + "".join(
        f"  parcel {i} waits at the north dock\n" for i in range(12)
    )
    assert _cut(notes, 120) == [
        (1, 4, ["notes", "lines 1-4"]),
        (5, 7, ["notes", "lines 5-7"]),
        (8, 10, ["notes", "lines 8-10"]),
        (11, 13, ["notes", "lines 11-13"]),
    ]


def test_long_line_is_cut_in_the_chunk_only() -> None:
    content = "title: " + "x" * 1000 + "\nshelf: A\n"
    [draft] = YamlDocumentChunker().chunk("stock.yaml", content)
    assert (draft.start_line, draft.end_line) == (1, 2)
    assert draft.content.split("\n") == [
        "# stock.yaml",
        ("title: " + "x" * 1000)[:400] + " …",
        "shelf: A",
    ]


def test_empty_file_has_no_chunks() -> None:
    assert YamlDocumentChunker().chunk("stock.yaml", "\n  \n") == []


@pytest.mark.parametrize(
    "path",
    [
        "config/stock.yaml",
        "warehouse/routes.yml",
        ".github/dependabot.yml",
        "STOCK.YAML",
    ],
)
def test_plain_yaml_file_is_a_yaml_document(path: str) -> None:
    assert classify_repo_document(path) is RepoDocumentKind.YAML


@pytest.mark.parametrize(
    "path",
    [
        "gen/stock.yaml",
        "api/generated/stock.yaml",
        "vendor/acme/stock.yaml",
        "third_party/stock.yaml",
        "pkg/testdata/stock.yaml",
        "pkg/fixtures/stock.yaml",
        "pkg/mocks/stock.yaml",
        "e2e/stock.yaml",
        "ui/__snapshots__/stock.yaml",
        "shelf/Tests/stock.yaml",
        "shelf_test/stock.yaml",
        "shelf_tests/stock.yaml",
        "shelf-tests/stock.yaml",
        "config/generated_stock.yaml",
        "pnpm-lock.yaml",
        "config/stock.lock.yml",
    ],
)
def test_generated_vendored_and_test_yaml_is_skipped(path: str) -> None:
    assert classify_repo_document(path) is None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (".github/workflows/ci.yml", RepoDocumentKind.WORKFLOW),
        ("deploy/values.yaml", RepoDocumentKind.CONFIG),
        ("examples/shipment.yaml", RepoDocumentKind.EXAMPLE),
        ("docker-compose.yml", RepoDocumentKind.CONFIG),
        ("docs/guide.md", RepoDocumentKind.REPO_DOC),
    ],
)
def test_yaml_rule_does_not_change_older_kinds(path: str, kind: RepoDocumentKind) -> None:
    assert classify_repo_document(path) is kind


class _RecordingLinker(RepoDocumentSymbolLinker):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []

    async def link_chunk_mentions(self, *, document_file_path, **kwargs):
        self.paths.append(document_file_path)
        return await super().link_chunk_mentions(
            document_file_path=document_file_path, **kwargs
        )


async def _repository(db_session) -> Repository:
    repository = Repository(
        host="example.com",
        git_url="git@example.com:acme/warehouse.git",
        name="warehouse",
        owner="acme",
        branch="main",
        status=RepositoryStatus.PENDING,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()
    return repository


async def _documents(db_session, repository) -> dict[str, RepoDocument]:
    return {
        document.file_path: document
        for document in (
            await db_session.scalars(
                select(RepoDocument).where(RepoDocument.repository_id == repository.id)
            )
        ).all()
    }


async def _index(db_session, repository, checkout, indexer=None):
    result = await (indexer or RepoDocumentIndexer()).index_checkout(
        session=db_session,
        repository_id=repository.id,
        checkout_path=checkout,
    )
    await db_session.commit()
    return result


STOCK = """\
# uses `helper` to count
bins:
  size: large
  rows: 12
"""


async def test_yaml_document_has_kind_title_line_chunks_and_no_mentions(
    db_session, tmp_path
):
    repository = await _repository(db_session)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "stock.yaml").write_text(STOCK, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Warehouse\n\nStock.\n", encoding="utf-8")
    linker = _RecordingLinker()
    indexer = RepoDocumentIndexer(symbol_linker=linker)

    await _index(db_session, repository, tmp_path, indexer)
    await _index(db_session, repository, tmp_path, indexer)  # relinks unchanged docs

    documents = await _documents(db_session, repository)
    stock = documents["config/stock.yaml"]
    assert stock.kind == "YAML"
    assert stock.title == "YAML: stock.yaml"
    assert documents["README.md"].kind == "Repo Doc"
    [chunk] = (
        await db_session.scalars(
            select(RepoDocumentChunk).where(RepoDocumentChunk.document_id == stock.id)
        )
    ).all()
    assert (chunk.start_line, chunk.end_line) == (1, 4)
    assert chunk.content.startswith("# config/stock.yaml\n")
    assert chunk.mentions == []
    assert set(linker.paths) == {"README.md"}


async def test_rerun_without_changes_updates_no_document(db_session, tmp_path):
    repository = await _repository(db_session)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "stock.yaml").write_text(STOCK, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Warehouse\n\nStock.\n", encoding="utf-8")
    await _index(db_session, repository, tmp_path)
    # An UPDATE would move `updated_at` off this date: search filters by it.
    await db_session.execute(
        update(RepoDocument)
        .where(RepoDocument.repository_id == repository.id)
        .values(updated_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    await db_session.commit()

    async def updated_at() -> dict[str, datetime]:
        rows = await db_session.execute(
            select(RepoDocument.file_path, RepoDocument.updated_at).where(
                RepoDocument.repository_id == repository.id
            )
        )
        return dict(rows.all())

    before = await updated_at()
    assert {stamp.year for stamp in before.values()} == {2020}

    result = await _index(db_session, repository, tmp_path)

    assert (result.indexed_documents, result.unchanged_documents) == (0, 2)
    assert await updated_at() == before


async def test_secret_like_values_are_redacted_and_lines_kept(db_session, tmp_path):
    repository = await _repository(db_session)
    original = """\
storage:
  db_password: "k8Hq2Lz9Wm4Rt7Xc"
  api_token: ${SHELF_API_TOKEN}
  admin_password: changeme
  label: k8Hq2Lz9Wm4Rt7Xc
docks:
  - shelf_token: abcdefgh12345678 # rotated
"""
    (tmp_path / "stock.yaml").write_text(original, encoding="utf-8")

    await _index(db_session, repository, tmp_path)

    stock = (await _documents(db_session, repository))["stock.yaml"]
    assert stock.content.splitlines() == [
        "storage:",
        "  db_password: <redacted>",
        "  api_token: ${SHELF_API_TOKEN}",
        "  admin_password: changeme",
        "  label: k8Hq2Lz9Wm4Rt7Xc",  # not a secret-named key
        "docks:",
        "  - shelf_token: <redacted>",
    ]
    # The hash is of the file as checked out, so an unchanged file stays unchanged.
    assert stock.content_hash == hashlib.sha256(original.encode("utf-8")).hexdigest()
    chunks = (
        await db_session.scalars(
            select(RepoDocumentChunk.content).where(
                RepoDocumentChunk.document_id == stock.id
            )
        )
    ).all()
    assert all("abcdefgh12345678" not in text for text in chunks)


async def test_files_that_cannot_be_read_or_hold_secrets_are_skipped(
    db_session, tmp_path
):
    repository = await _repository(db_session)
    config = tmp_path / "config"
    config.mkdir()
    (config / "keys.yaml").write_text(
        "key: |\n  -----BEGIN PRIVATE KEY-----\n  abc\n", encoding="utf-8"
    )
    (config / "shelf-secret.yaml").write_text(
        "apiVersion: v1\nkind: Secret\ndata:\n  a: b\n", encoding="utf-8"
    )
    (config / "sealed.yaml").write_text(
        "kind: SealedSecret\nspec: {}\n", encoding="utf-8"
    )
    (config / "sops.yaml").write_text("a: ENC[x]\nsops:\n  version: 3\n", encoding="utf-8")
    (config / "vault.yml").write_text("$ANSIBLE_VAULT;1.1;AES256\n6162\n", encoding="utf-8")
    (config / "big.yaml").write_text("shelf: A\n" * 30_000, encoding="utf-8")
    (config / "latin.yaml").write_bytes("shelf: caf\xe9\n".encode("latin-1"))
    (config / "nul.yaml").write_text("shelf: A\x00\n", encoding="utf-8")
    (tmp_path / "latin.md").write_bytes("# caf\xe9\n".encode("latin-1"))
    (tmp_path / "README.md").write_text("# Warehouse\n", encoding="utf-8")

    result = await _index(db_session, repository, tmp_path)

    assert set(await _documents(db_session, repository)) == {"README.md"}
    assert result.indexed_documents == 1


async def test_file_that_stops_passing_loses_its_document(db_session, tmp_path):
    repository = await _repository(db_session)
    stock = tmp_path / "stock.yaml"
    stock.write_text(STOCK, encoding="utf-8")
    await _index(db_session, repository, tmp_path)
    assert set(await _documents(db_session, repository)) == {"stock.yaml"}

    stock.write_text(STOCK + "kind: Secret\n", encoding="utf-8")
    result = await _index(db_session, repository, tmp_path)

    assert await _documents(db_session, repository) == {}
    assert result.deleted_documents == 1
    assert (await db_session.scalars(select(RepoDocumentChunk))).all() == []
