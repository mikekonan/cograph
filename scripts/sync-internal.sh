#!/usr/bin/env bash
# Mirror pgw-main → github main, scrubbing pgw-internal infra (the pgw-ci
# workflow file + any pgw-tagged comments). Run from the repo root.
#
# Why this exists: pgw.dev/ai/cograph holds the internal CI workflow
# (.github/workflows/pgw-ci.yml) plus pgw-flavored comments. The public
# github.com/mikekonan/cograph mirror must not contain those. A plain
# `git push github pgw-main:main` would re-introduce them; this script
# filters them out commit-by-commit before pushing.
#
# Requires: git, git-filter-repo (brew install git-filter-repo).
# Pushes: github (mikekonan/cograph) — main branch only, force-with-lease.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if ! git remote get-url github >/dev/null 2>&1; then
  echo "github remote missing — run: git remote add github git@github.com:mikekonan/cograph.git" >&2
  exit 1
fi

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "git-filter-repo not installed — run: brew install git-filter-repo" >&2
  exit 1
fi

WORK_DIR="$(mktemp -d -t cograph-sync-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

git clone --bare "$PWD" "$WORK_DIR/cograph.git" >/dev/null 2>&1
cd "$WORK_DIR/cograph.git"

# 1. Drop pgw-internal files from every commit they ever touched:
#    - .github/workflows/pgw-ci.yml — internal CI workflow
#    - scripts/sync_to_github.sh — this script itself (pgw-only tooling)
git filter-repo \
  --invert-paths \
  --path .github/workflows/pgw-ci.yml \
  --path scripts/sync_to_github.sh \
  --force >/dev/null

# 2. Rewrite commit messages: drop "ci(pgw):" prefix, replace "pgw staging"
#    references with generic CI phrasing.
cat > /tmp/cograph-msg-cb.py <<'PYEOF'
import re

msg = commit.message.decode('utf-8', errors='replace')

if msg.startswith('ci(pgw): move runners to staging label + biome format fix'):
    commit.message = (
        b"fe: biome formatter wrap fix on AdminIdentityProvidersPage\n"
        b"\n"
        b"Restore single-paragraph wrap on a checkbox label so the file\n"
        b"is biome-clean.\n"
    )
else:
    msg = re.sub(r'\bpgw\s+staging\b', 'shared CI', msg, flags=re.IGNORECASE)
    msg = re.sub(r'\bstaging\s+runner\b', 'shared CI runner', msg, flags=re.IGNORECASE)
    msg = re.sub(r'\bpgw\b', 'shared CI', msg, flags=re.IGNORECASE)
    commit.message = msg.encode('utf-8')
PYEOF
git filter-repo --commit-callback "$(cat /tmp/cograph-msg-cb.py)" --force --refs HEAD >/dev/null
rm -f /tmp/cograph-msg-cb.py

# 3. Push the scrubbed HEAD to github main (force-with-lease against current
#    remote HEAD — refuses if someone else pushed in the meantime).
NEW_HEAD="$(git rev-parse HEAD)"
GITHUB_HEAD="$(git ls-remote git@github.com:mikekonan/cograph.git refs/heads/main | awk '{print $1}')"

if [ -z "$GITHUB_HEAD" ]; then
  git push --force git@github.com:mikekonan/cograph.git "HEAD:refs/heads/main"
else
  git push --force-with-lease="refs/heads/main:$GITHUB_HEAD" \
    git@github.com:mikekonan/cograph.git "HEAD:refs/heads/main"
fi

echo ""
echo "Scrubbed history pushed to github main."
echo "  pgw-main HEAD: $(cd "$OLDPWD" && git rev-parse origin/pgw-main)"
echo "  github main HEAD (scrubbed): $NEW_HEAD"
