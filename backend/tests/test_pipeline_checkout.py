from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from git import Actor, Repo

from backend.app.pipeline.checkout import GitCheckoutAdapter

_ACTOR = Actor("Cograph Tests", "tests@example.com")


def _commit_file(
    repo: Repo,
    repo_path: Path,
    relative_path: str,
    content: str,
) -> str:
    target_path = repo_path / relative_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    repo.index.add([relative_path])
    return repo.index.commit("update", author=_ACTOR, committer=_ACTOR).hexsha


def _init_source_repo(repo_path: Path) -> Repo:
    repo = Repo.init(repo_path)
    repo.git.checkout("-B", "main")
    return repo


async def test_git_checkout_adapter_clones_and_refreshes_branch_head(tmp_path):
    source_repo_path = tmp_path / "source"
    source_repo = _init_source_repo(source_repo_path)
    first_commit = _commit_file(
        source_repo,
        source_repo_path,
        "service.py",
        "def value() -> int:\n    return 1\n",
    )

    adapter = GitCheckoutAdapter(checkouts_root=tmp_path / "checkouts")
    repository_id = uuid4()

    first_checkout = await adapter.prepare_checkout(
        repository_id=repository_id,
        git_url=str(source_repo_path),
        branch="main",
    )

    assert first_checkout.requested_ref == first_commit
    assert (first_checkout.path / "service.py").read_text(encoding="utf-8").strip().endswith(
        "return 1"
    )

    second_commit = _commit_file(
        source_repo,
        source_repo_path,
        "service.py",
        "def value() -> int:\n    return 2\n",
    )

    second_checkout = await adapter.prepare_checkout(
        repository_id=repository_id,
        git_url=str(source_repo_path),
        branch="main",
    )

    assert second_checkout.path == first_checkout.path
    assert second_checkout.requested_ref == second_commit
    assert (second_checkout.path / "service.py").read_text(encoding="utf-8").strip().endswith(
        "return 2"
    )
