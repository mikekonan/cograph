"""Tests for GET /api/repos/:host/:owner/:name/docs and GET /api/repos/:host/:owner/:name/docs/:slug.

Coverage
--------
- Tree on READY repo with documents → matches repo-doc FE shape
- Tree on READY repo with no documents → empty items array
- Tree on non-READY repo → 409 REPO_NOT_READY
- Tree on unknown repo → 404 NOT_FOUND
- Page by valid slug → shape parity with the repo-doc page contract
- Page by unknown slug → 404 NOT_FOUND
- Page on non-READY repo → 409 REPO_NOT_READY
- Slug collision handling: two paths that produce the same base slug
- related_nodes populated from chunk mentions
- related_nodes deduplicated (same node referenced in multiple chunks)
- Slug round-trip: list tree → pick leaf slug → GET page → correct doc
"""
from __future__ import annotations


from backend.app.models.code_node import CodeNode
from backend.app.models.enums import CodeNodeType, RepositoryStatus, RepositoryVisibility, SyncSchedule
from backend.app.models.repo_document import RepoDocument, RepoDocumentChunk
from backend.app.models.repository import Repository
from backend.app.repo_docs.slug import file_path_to_slug, build_slug_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(
    db_session,
    *,
    status: RepositoryStatus = RepositoryStatus.READY,
    visibility: RepositoryVisibility = RepositoryVisibility.PUBLIC,
):
    repo = Repository(
        host="example.com",
        git_url="https://example.com/acme/wiki-test.git",
        name="wiki-test",
        owner="acme",
        branch="main",
        status=status,
        visibility=visibility,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repo)
    return repo


def _make_doc(db_session, *, repository_id, file_path: str, title: str | None = None, content: str = "# Doc"):
    doc = RepoDocument(
        repository_id=repository_id,
        file_path=file_path,
        title=title,
        content=content,
        content_hash=f"hash-{file_path}",
        bytes=len(content),
    )
    db_session.add(doc)
    return doc


def _make_node(db_session, *, repository_id, name: str, file_path: str = "module.py",
               start_line: int = 1, end_line: int = 10):
    node = CodeNode(
        repository_id=repository_id,
        file_path=file_path,
        qualified_name=f"{file_path}:{name}",
        node_type=CodeNodeType.FUNCTION,
        name=name,
        language="python",
        start_line=start_line,
        end_line=end_line,
        content=f"def {name}(): pass",
        signature=f"{name}()",
        doc_comment=None,
        callers=[],
        callees=[],
        node_metadata={},
        content_hash=f"hash-{name}",
    )
    db_session.add(node)
    return node


def _make_chunk(db_session, *, document_id, chunk_index: int = 0, mentions: list[str] | None = None):
    chunk = RepoDocumentChunk(
        document_id=document_id,
        chunk_index=chunk_index,
        heading_path=[],
        content="chunk content",
        mentions=mentions or [],
    )
    db_session.add(chunk)
    return chunk


# ---------------------------------------------------------------------------
# Slug unit tests (no DB needed)
# ---------------------------------------------------------------------------


def test_slug_readme():
    assert file_path_to_slug("README.md") == "readme"


def test_slug_strips_docs_prefix():
    assert file_path_to_slug("docs/installation.md") == "installation"


def test_slug_nested_path():
    assert file_path_to_slug("docs/guides/setup.md") == "guides-setup"


def test_slug_non_md_extension_kept():
    result = file_path_to_slug("src/auth/login.py")
    assert result == "src-auth-login-py"


def test_slug_collision_handling():
    import uuid as _uuid
    id_a = _uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    id_b = _uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
    # Two paths that produce the same base slug.
    items = [
        (id_a, "docs/overview.md"),   # → overview
        (id_b, "overview.md"),        # → overview (collision)
    ]
    mapping = build_slug_map(items)
    # Both slugs must be present and distinct.
    assert len(mapping) == 2
    slugs = list(mapping.keys())
    assert slugs[0] != slugs[1]
    # The colliding one gets a -xxxx suffix.
    assert any("-" in s for s in slugs)
    # Both UUIDs must be reachable.
    assert set(mapping.values()) == {id_a, id_b}


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


async def test_get_docs_tree_empty_when_no_documents(client, db_session):
    """READY repo with no documents returns empty items array, not an error."""
    repo = _make_repo(db_session)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


async def test_get_docs_tree_repo_not_found(client, db_session):
    await db_session.commit()

    response = await client.get("/api/repos/example.com/missing/repo/docs")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_docs_tree_returns_409_while_indexing(client, db_session):
    """Docs tree returns 409 REPO_NOT_READY while repo is not ready."""
    repo = _make_repo(db_session, status=RepositoryStatus.INDEXING)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPO_NOT_READY"


async def test_get_docs_tree_hides_admin_only_repo_from_anonymous(client, db_session):
    repo = _make_repo(db_session, visibility=RepositoryVisibility.ADMIN_ONLY)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_docs_tree_shape_parity(client, db_session):
    """Tree response matches the repo-doc DocTreeNode shape exactly."""
    repo = _make_repo(db_session)
    await db_session.flush()

    _make_doc(db_session, repository_id=repo.id, file_path="README.md", title="Overview")
    _make_doc(db_session, repository_id=repo.id, file_path="docs/installation.md", title="Installation")
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) >= 1

    def _check_node(node):
        assert "id" in node
        assert "title" in node
        assert "slug" in node
        assert "doc_type" in node
        assert "sort_order" in node
        assert "parent_id" in node
        assert "children" in node
        assert isinstance(node["children"], list)
        for child in node["children"]:
            _check_node(child)

    for item in data["items"]:
        _check_node(item)


async def test_get_docs_tree_groups_by_directory(client, db_session):
    """Files in the same directory are placed under a synthetic ``_dir-`` group node."""
    repo = _make_repo(db_session)
    await db_session.flush()

    _make_doc(db_session, repository_id=repo.id, file_path="guides/getting-started.md")
    _make_doc(db_session, repository_id=repo.id, file_path="guides/advanced.md")
    _make_doc(db_session, repository_id=repo.id, file_path="README.md")
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")

    assert response.status_code == 200
    items = response.json()["items"]

    # README.md is pinned at the top of the root level.
    assert items[0]["slug"] == "readme"
    assert items[0]["file_path"] == "README.md"

    # guides/* should be grouped under a "Guides" `_dir-` group node.
    group_nodes = [item for item in items if item["children"]]
    assert len(group_nodes) >= 1
    guides_group = next(
        (g for g in group_nodes if g["slug"].startswith("_dir-guides")), None
    )
    assert guides_group is not None
    assert guides_group["title"] == "Guides"
    assert guides_group["file_path"] is None
    child_slugs = {c["slug"] for c in guides_group["children"]}
    assert any("getting-started" in s for s in child_slugs)
    assert any("advanced" in s for s in child_slugs)
    # Each leaf carries its real file_path so the FE can render the
    # filesystem mirror directly.
    child_paths = {c["file_path"] for c in guides_group["children"]}
    assert child_paths == {"guides/getting-started.md", "guides/advanced.md"}


async def test_get_docs_tree_mirrors_nested_directories(client, db_session):
    """The tree mirrors the repo's filesystem hierarchy recursively."""
    repo = _make_repo(db_session)
    await db_session.flush()

    _make_doc(db_session, repository_id=repo.id, file_path="docs/architecture/overview.md")
    _make_doc(db_session, repository_id=repo.id, file_path="docs/api/auth.md")
    _make_doc(db_session, repository_id=repo.id, file_path="docs/api/users.md")
    _make_doc(db_session, repository_id=repo.id, file_path="README.md")
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")
    items = response.json()["items"]

    # README pinned at the root.
    assert items[0]["slug"] == "readme"
    # `docs/` is a top-level group.
    docs_group = next(g for g in items if g["slug"] == "_dir-docs")
    assert docs_group["title"] == "Docs"
    assert docs_group["file_path"] is None

    # `docs/api/` and `docs/architecture/` are sub-groups under `docs/`.
    sub_slugs = {c["slug"] for c in docs_group["children"]}
    assert "_dir-docs/api" in sub_slugs
    assert "_dir-docs/architecture" in sub_slugs

    # `docs/api/auth.md` is a leaf inside the `api/` sub-group with the
    # full file_path preserved (the FE addresses by path).
    api_group = next(c for c in docs_group["children"] if c["slug"] == "_dir-docs/api")
    api_paths = {c["file_path"] for c in api_group["children"]}
    assert api_paths == {"docs/api/auth.md", "docs/api/users.md"}


async def test_get_doc_page_by_valid_slug(client, db_session):
    """GET page by slug returns the full repo-doc page shape."""
    repo = _make_repo(db_session)
    await db_session.flush()

    node = _make_node(db_session, repository_id=repo.id, name="setup", file_path="setup.py", start_line=5, end_line=20)
    doc = _make_doc(
        db_session,
        repository_id=repo.id,
        file_path="README.md",
        title="Overview",
        content="# Overview\n\nSee `setup`.",
    )
    await db_session.flush()

    _make_chunk(db_session, document_id=doc.id, mentions=[str(node.id)])
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/readme")

    assert response.status_code == 200
    data = response.json()

    # Required top-level fields.
    assert data["id"] == str(doc.id)
    assert data["title"] == "Overview"
    assert data["slug"] == "readme"
    assert data["content"] == "# Overview\n\nSee `setup`."
    assert data["doc_type"] in ("overview", "module", "api", "guide")
    assert isinstance(data["sort_order"], int)
    assert "parent_id" in data
    assert "created_at" in data
    assert "updated_at" in data

    # related_nodes shape.
    assert len(data["related_nodes"]) == 1
    rn = data["related_nodes"][0]
    assert rn["id"] == str(node.id)
    assert rn["name"] == "setup"
    assert rn["node_type"] == "function"
    assert rn["file_path"] == "setup.py"
    assert rn["start_line"] == 5
    assert rn["end_line"] == 20


async def test_get_doc_page_not_found(client, db_session):
    repo = _make_repo(db_session)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/nonexistent-slug-xyz")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_get_doc_page_returns_409_while_indexing(client, db_session):
    """Doc page returns 409 REPO_NOT_READY while repo is not yet ready."""
    repo = _make_repo(db_session, status=RepositoryStatus.EMBEDDING)
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/readme")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPO_NOT_READY"


async def test_get_doc_page_hides_admin_only_repo_from_anonymous(client, db_session):
    repo = _make_repo(db_session, visibility=RepositoryVisibility.ADMIN_ONLY)
    await db_session.flush()

    _make_doc(db_session, repository_id=repo.id, file_path="README.md", title="Overview")
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/readme")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_related_nodes_deduplicated(client, db_session):
    """Same CodeNode referenced in two chunks appears only once in related_nodes."""
    repo = _make_repo(db_session)
    await db_session.flush()

    node = _make_node(db_session, repository_id=repo.id, name="helper")
    doc = _make_doc(db_session, repository_id=repo.id, file_path="README.md")
    await db_session.flush()

    _make_chunk(db_session, document_id=doc.id, chunk_index=0, mentions=[str(node.id)])
    _make_chunk(db_session, document_id=doc.id, chunk_index=1, mentions=[str(node.id)])
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/readme")

    assert response.status_code == 200
    related = response.json()["related_nodes"]
    ids = [rn["id"] for rn in related]
    assert ids.count(str(node.id)) == 1


async def test_slug_round_trip(client, db_session):
    """List tree → pick a leaf slug → GET page → get the correct document."""
    repo = _make_repo(db_session)
    await db_session.flush()

    doc_a = _make_doc(db_session, repository_id=repo.id, file_path="docs/auth.md", title="Auth")
    doc_b = _make_doc(db_session, repository_id=repo.id, file_path="docs/config.md", title="Config")
    await db_session.commit()

    # Get tree.
    tree_resp = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")
    assert tree_resp.status_code == 200

    # Collect all leaf slugs from the tree.
    def _collect_leaves(nodes):
        leaves = []
        for n in nodes:
            if not n["children"]:
                leaves.append(n)
            else:
                leaves.extend(_collect_leaves(n["children"]))
        return leaves

    leaves = _collect_leaves(tree_resp.json()["items"])
    leaf_slugs = {leaf["slug"] for leaf in leaves}

    # Each leaf slug must resolve to a valid page.
    resolved_doc_ids = set()
    for slug in leaf_slugs:
        page_resp = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/{slug}")
        assert page_resp.status_code == 200, f"slug={slug} returned {page_resp.status_code}"
        resolved_doc_ids.add(page_resp.json()["id"])

    # Both documents must be reachable.
    assert str(doc_a.id) in resolved_doc_ids
    assert str(doc_b.id) in resolved_doc_ids


async def test_slug_collision_resolved_in_api(client, db_session):
    """Two file_paths that produce the same base slug both return pages."""
    repo = _make_repo(db_session)
    await db_session.flush()

    # "docs/overview.md" and "overview.md" both produce base slug "overview".
    doc_a = _make_doc(db_session, repository_id=repo.id, file_path="docs/overview.md", title="Docs Overview")
    doc_b = _make_doc(db_session, repository_id=repo.id, file_path="overview.md", title="Root Overview")
    await db_session.commit()

    tree_resp = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")
    assert tree_resp.status_code == 200

    def _collect_leaves(nodes):
        leaves = []
        for n in nodes:
            if not n["children"]:
                leaves.append(n)
            else:
                leaves.extend(_collect_leaves(n["children"]))
        return leaves

    leaves = _collect_leaves(tree_resp.json()["items"])
    slugs = [leaf["slug"] for leaf in leaves]
    # All slugs must be distinct.
    assert len(slugs) == len(set(slugs)), f"Duplicate slugs: {slugs}"
    # Both pages must be reachable.
    found_ids = set()
    for slug in slugs:
        resp = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs/{slug}")
        assert resp.status_code == 200
        found_ids.add(resp.json()["id"])
    assert str(doc_a.id) in found_ids
    assert str(doc_b.id) in found_ids


async def test_get_docs_tree_total_equals_document_count(client, db_session):
    """total in tree response equals the number of indexed documents (leaf pages)."""
    repo = _make_repo(db_session)
    await db_session.flush()

    _make_doc(db_session, repository_id=repo.id, file_path="README.md")
    _make_doc(db_session, repository_id=repo.id, file_path="docs/auth.md")
    _make_doc(db_session, repository_id=repo.id, file_path="docs/config.md")
    await db_session.commit()

    response = await client.get(f"/api/repos/{repo.host}/{repo.owner}/{repo.name}/docs")

    assert response.status_code == 200
    data = response.json()
    # total must equal the number of stored RepoDocument rows (not synthetic group nodes).
    assert data["total"] == 3
    # Confirm items are present (tree may have group nodes wrapping some leaves).
    leaf_count = sum(
        len(item["children"]) if item["children"] else 1
        for item in data["items"]
    )
    assert leaf_count == data["total"]
