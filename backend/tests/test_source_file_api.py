from __future__ import annotations

import httpx

from backend.app.models.enums import RepositoryStatus, RepositoryVisibility, SourceFileKind, SyncSchedule
from backend.app.models.repository import Repository
from backend.app.models.source_file import SourceFile


async def _seed_source_file(
    db_session,
    *,
    raw_bytes: bytes,
    file_path: str = "service.py",
    language: str = "python",
    visibility: RepositoryVisibility = RepositoryVisibility.PUBLIC,
) -> tuple[Repository, SourceFile]:
    repository = Repository(
        host="example.com",
        git_url="git@github.com:mikekonan/cograph.git",
        name="cograph",
        owner="mikekonan",
        branch="main",
        status=RepositoryStatus.READY,
        visibility=visibility,
        sync_schedule=SyncSchedule.MANUAL,
    )
    db_session.add(repository)
    await db_session.flush()

    source_file = SourceFile(
        repository_id=repository.id,
        file_path=file_path,
        language=language,
        kind=SourceFileKind.CODE.value,
        raw_bytes=raw_bytes,
        content_hash="deadbeef",
        blob_hash="deadbeef",
        bytes=len(raw_bytes),
    )
    db_session.add(source_file)
    await db_session.commit()
    return repository, source_file


async def test_get_source_file_returns_full_content(db_session, client: httpx.AsyncClient):
    raw = b"def hello() -> str:\n    return 'hi'\n"
    repository, source_file = await _seed_source_file(db_session, raw_bytes=raw)

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == raw.decode("utf-8")
    assert body["bytes"] == len(raw)
    assert body["language"] == "python"


async def test_get_source_file_returns_404_for_unknown_id(
    db_session, client: httpx.AsyncClient
):
    raw = b"pass\n"
    repository, _ = await _seed_source_file(db_session, raw_bytes=raw)

    missing_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{missing_id}")
    assert response.status_code == 404


async def test_get_source_file_hides_admin_only_repo_from_anonymous(
    db_session,
    client: httpx.AsyncClient,
):
    repository, source_file = await _seed_source_file(
        db_session,
        raw_bytes=b"pass\n",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )

    response = await client.get(f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}")

    assert response.status_code == 404


async def test_get_source_file_range_returns_exact_slice(
    db_session, client: httpx.AsyncClient
):
    raw = b"abcdefghij"
    repository, source_file = await _seed_source_file(db_session, raw_bytes=raw)

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}/range",
        params={"start": 2, "end": 6},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "cdef"
    assert body["bytes"] == 4
    assert body["start_byte"] == 2
    assert body["end_byte"] == 6


async def test_get_source_file_range_clamps_end_to_file_length(
    db_session, client: httpx.AsyncClient
):
    raw = b"abcdef"
    repository, source_file = await _seed_source_file(db_session, raw_bytes=raw)

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}/range",
        params={"start": 4, "end": 100},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "ef"
    assert body["end_byte"] == 6


async def test_get_source_file_range_rejects_inverted_range(
    db_session, client: httpx.AsyncClient
):
    raw = b"abcdef"
    repository, source_file = await _seed_source_file(db_session, raw_bytes=raw)

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}/range",
        params={"start": 5, "end": 1},
    )
    assert response.status_code == 400


async def test_get_source_file_range_rejects_split_multibyte_char(
    db_session, client: httpx.AsyncClient
):
    raw = "Привет".encode("utf-8")
    repository, source_file = await _seed_source_file(db_session, raw_bytes=raw)

    # The first character "П" is a 2-byte sequence (0xD0 0x9F). Slicing
    # (0, 1) produces an incomplete UTF-8 sequence.
    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}/range",
        params={"start": 0, "end": 1},
    )
    assert response.status_code == 400


async def test_get_source_file_range_hides_admin_only_repo_from_anonymous(
    db_session,
    client: httpx.AsyncClient,
):
    repository, source_file = await _seed_source_file(
        db_session,
        raw_bytes=b"abcdef",
        visibility=RepositoryVisibility.ADMIN_ONLY,
    )

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}/files/{source_file.id}/range",
        params={"start": 0, "end": 3},
    )

    assert response.status_code == 404
