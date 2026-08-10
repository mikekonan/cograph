from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from backend.app.git.askpass import askpass_env
from backend.app.git.credentials import redact_token


class GitCheckoutError(Exception):
    pass


@dataclass(slots=True, kw_only=True)
class PreparedCheckout:
    path: Path
    requested_ref: str
    resolved_branch: str


class GitCheckoutAdapter:
    def __init__(self, *, checkouts_root: Path) -> None:
        self._checkouts_root = Path(checkouts_root)

    async def prepare_checkout(
        self,
        *,
        repository_id: UUID,
        git_url: str,
        branch: str | None,
        requested_ref: str | None = None,
        plaintext_token: str | None = None,
    ) -> PreparedCheckout:
        return await asyncio.to_thread(
            self._prepare_checkout_sync,
            repository_id,
            git_url,
            branch,
            requested_ref,
            plaintext_token,
        )

    def _prepare_checkout_sync(
        self,
        repository_id: UUID,
        git_url: str,
        branch: str | None,
        requested_ref: str | None,
        plaintext_token: str | None,
    ) -> PreparedCheckout:
        self._checkouts_root.mkdir(parents=True, exist_ok=True)
        checkout_path = self._checkouts_root / str(repository_id)

        # If no branch was specified, auto-detect the remote default branch.
        if branch is None:
            branch = _detect_default_branch(git_url, plaintext_token=plaintext_token)

        with _credential_env(plaintext_token) as env:
            repo = self._open_or_clone_checkout(
                checkout_path=checkout_path,
                git_url=git_url,
                branch=branch,
                env=env,
            )

            try:
                # GitPython lets us pass env per-call; the askpass shim is
                # only effective when the env reaches the spawned `git`.
                if env is not None:
                    repo.remotes.origin.fetch(prune=True, env=env)
                else:
                    repo.remotes.origin.fetch(prune=True)
                resolved_ref = self._checkout_target(
                    repo=repo,
                    branch=branch,
                    requested_ref=requested_ref,
                )
            except GitCommandError as exc:
                raise GitCheckoutError(
                    _format_git_error(
                        "Failed to prepare checkout", exc, plaintext_token
                    )
                ) from exc

        return PreparedCheckout(
            path=checkout_path,
            requested_ref=resolved_ref,
            resolved_branch=branch,
        )

    def _open_or_clone_checkout(
        self,
        *,
        checkout_path: Path,
        git_url: str,
        branch: str,
        env: dict[str, str] | None,
    ) -> Repo:
        if checkout_path.exists():
            try:
                repo = Repo(checkout_path)
            except (InvalidGitRepositoryError, NoSuchPathError):
                self._reset_checkout_dir(checkout_path)
                return self._clone_checkout(
                    checkout_path=checkout_path,
                    git_url=git_url,
                    branch=branch,
                    env=env,
                )

            if _origin_url(repo) != git_url:
                self._reset_checkout_dir(checkout_path)
                return self._clone_checkout(
                    checkout_path=checkout_path,
                    git_url=git_url,
                    branch=branch,
                    env=env,
                )
            return repo

        return self._clone_checkout(
            checkout_path=checkout_path,
            git_url=git_url,
            branch=branch,
            env=env,
        )

    def _clone_checkout(
        self,
        *,
        checkout_path: Path,
        git_url: str,
        branch: str,
        env: dict[str, str] | None,
    ) -> Repo:
        try:
            kwargs: dict[str, object] = {"branch": branch, "single_branch": True}
            if env is not None:
                kwargs["env"] = env
            return Repo.clone_from(git_url, checkout_path, **kwargs)
        except GitCommandError as exc:
            # Token is held only in `env` here, never in argv. The
            # GitPython error includes argv but not env, so the secret
            # cannot leak via `exc.command`. We still scrub the message
            # defensively in case a future GitPython release changes that.
            token = (env or {}).get("GIT_PASSWORD")
            raise GitCheckoutError(
                _format_git_error("Failed to clone repository", exc, token)
            ) from exc

    def _checkout_target(
        self,
        *,
        repo: Repo,
        branch: str,
        requested_ref: str | None,
    ) -> str:
        if requested_ref is None:
            tracking_ref = f"origin/{branch}"
            repo.git.checkout("--force", branch)
            repo.git.reset("--hard", tracking_ref)
            return repo.commit(tracking_ref).hexsha

        repo.git.checkout("--force", requested_ref)
        return repo.head.commit.hexsha

    def _reset_checkout_dir(self, checkout_path: Path) -> None:
        if checkout_path.exists():
            shutil.rmtree(checkout_path)


def _origin_url(repo: Repo) -> str | None:
    try:
        return next(iter(repo.remotes.origin.urls))
    except (AttributeError, IndexError, StopIteration):
        return None


def _format_git_error(
    prefix: str, exc: GitCommandError, plaintext_token: str | None = None
) -> str:
    details = exc.stderr or exc.stdout or str(exc)
    return redact_token(f"{prefix}: {details.strip()}", plaintext_token)


@contextlib.contextmanager
def _credential_env(
    plaintext_token: str | None,
) -> Iterator[dict[str, str] | None]:
    """Yield an env dict primed for `GIT_ASKPASS` when a token is given,
    or None otherwise (legacy public-clone path)."""
    if plaintext_token is None:
        yield None
        return
    with askpass_env(plaintext_token=plaintext_token) as env:
        yield env


def _detect_default_branch(
    git_url: str, *, plaintext_token: str | None = None
) -> str:
    """Ask the remote for its HEAD symbolic ref to find the default branch.

    Runs ``git ls-remote --symref <url> HEAD`` and parses the output line:
        ref: refs/heads/master\tHEAD
    Falls back to ``"main"`` when the remote is unreachable or the output is
    in an unexpected format — the caller will get a proper GitCheckoutError on
    the subsequent clone if even that fails.
    """
    try:
        with _credential_env(plaintext_token) as env:
            result = subprocess.run(
                ["git", "ls-remote", "--symref", git_url, "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
        for line in result.stdout.splitlines():
            m = re.match(r"^ref:\s+refs/heads/([^\t\s]+)\s+HEAD", line)
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return "main"
