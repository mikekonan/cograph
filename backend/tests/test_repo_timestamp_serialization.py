from __future__ import annotations

from datetime import UTC, datetime

from backend.app.models.enums import RepositoryVisibility
from backend.app.models.repository import Repository


async def test_list_repositories_serializes_last_synced_at_as_utc_z(client, db_session):
    last_synced_at = datetime(2026, 4, 23, 2, 0, 0, tzinfo=UTC)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/scheduled.git",
        name="scheduled",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        last_synced_at=last_synced_at,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get("/api/repos")

    assert response.status_code == 200
    assert response.json()["items"][0]["last_synced_at"] == last_synced_at.isoformat().replace(
        "+00:00", "Z"
    )


async def test_get_repository_serializes_last_synced_at_as_utc_z(client, db_session):
    last_synced_at = datetime(2026, 4, 23, 2, 0, 0, tzinfo=UTC)
    repository = Repository(
        host="example.com",
        git_url="https://github.com/acme/demo.git",
        name="demo",
        owner="acme",
        branch="main",
        visibility=RepositoryVisibility.PUBLIC,
        last_synced_at=last_synced_at,
    )
    db_session.add(repository)
    await db_session.commit()

    response = await client.get(
        f"/api/repos/{repository.host}/{repository.owner}/{repository.name}"
    )

    assert response.status_code == 200
    assert response.json()["last_synced_at"] == last_synced_at.isoformat().replace("+00:00", "Z")
