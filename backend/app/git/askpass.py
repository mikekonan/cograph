"""`GIT_ASKPASS` shim — let git ask the env for credentials.

Why not URL-embedded creds? `https://x-access-token:TOKEN@github.com/...`
leaks the token into `git_url`, `.git/config`, error messages, and
`ps aux`. `GIT_ASKPASS` keeps the secret in env only — once the script
and env vanish, the secret is gone with them.

Why a tmpfile, not a static script in the repo? GIT executes whatever
`GIT_ASKPASS` points to as a binary. Writing a 0700 tmpfile per clone
(deleted in `finally`) is simpler than reasoning about a static path's
write permissions across deployments.
"""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

# The askpass script itself contains no secret. It dispatches on the
# prompt git supplies (Username / Password) and returns the matching env
# variable. Both env vars are set by the caller per-clone.
_ASKPASS_BODY = b"""#!/bin/sh
case "$1" in
  Username*) printf '%s' "$GIT_USERNAME" ;;
  Password*) printf '%s' "$GIT_PASSWORD" ;;
esac
"""


@contextmanager
def askpass_env(
    *, plaintext_token: str, username: str = "x-access-token"
) -> Iterator[dict[str, str]]:
    """Yield an env dict that primes git for password-less HTTPS clone.

    Caller passes the dict into `subprocess.run(... env=...)` or
    `Repo.clone_from(... env=...)`. On exit the tmpfile is unlinked even
    if the clone raises.

    The dict mutates a copy of the current environment with three new
    keys: `GIT_ASKPASS`, `GIT_USERNAME`, `GIT_PASSWORD`. We also set
    `GIT_TERMINAL_PROMPT=0` so a missing askpass never falls back to a
    blocking tty prompt.
    """
    tmp = tempfile.NamedTemporaryFile(
        prefix="cograph-askpass-", suffix=".sh", delete=False
    )
    try:
        tmp.write(_ASKPASS_BODY)
        tmp.close()
        os.chmod(tmp.name, stat.S_IRWXU)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = tmp.name
        env["GIT_USERNAME"] = username
        env["GIT_PASSWORD"] = plaintext_token
        yield env
    finally:
        Path(tmp.name).unlink(missing_ok=True)
