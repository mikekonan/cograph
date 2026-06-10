#!/usr/bin/env bash
# CI guard: a diff that touches the wiki quality-surface modules must
# either bump WIKI_SCHEMA_VERSION or carry [wiki-schema-no-bump] in a
# commit message (the escape hatch for pure refactors / logging /
# telemetry changes that genuinely don't move LLM output).
#
# This is the blunt companion to the precise unit fingerprint in
# backend/tests/unit/wiki/test_schema_version_guard.py: the unit test
# catches changes to prompts/budgets/hash algorithms exactly; this script
# forces every PR touching these files to make an explicit choice.
#
# Usage: scripts/check_wiki_schema_version.sh <base-sha>
set -euo pipefail

BASE_REF="${1:?usage: check_wiki_schema_version.sh <base-sha>}"

SURFACE_PATHS=(
  "backend/app/wiki/prompts.py"
  "backend/app/wiki/pipeline.py"
  "backend/app/wiki/incremental.py"
)
VERSION_FILE="backend/app/wiki/version.py"

changed=$(git diff --name-only "${BASE_REF}...HEAD" -- "${SURFACE_PATHS[@]}")
if [[ -z "${changed}" ]]; then
  echo "No wiki quality-surface files touched — OK."
  exit 0
fi

echo "Wiki quality-surface files changed:"
echo "${changed}"

if git log --format=%B "${BASE_REF}..HEAD" | grep -qF '[wiki-schema-no-bump]'; then
  echo "Found [wiki-schema-no-bump] in a commit message — skipping the bump check."
  exit 0
fi

if git diff "${BASE_REF}...HEAD" -- "${VERSION_FILE}" \
  | grep -qE '^\+WIKI_SCHEMA_VERSION = '; then
  echo "WIKI_SCHEMA_VERSION bumped — OK."
  exit 0
fi

cat >&2 <<'EOF'
::error::This PR changes wiki quality-surface files without bumping WIKI_SCHEMA_VERSION.

If the change affects what the LLM would produce for the same repo state
(prompts, gate budgets, reuse-hash algorithms, plan normalization):
  1. Bump WIKI_SCHEMA_VERSION in backend/app/wiki/version.py.
  2. Append the new sha to SURFACE_SHA_HISTORY:
     python -c "from backend.app.wiki.version import compute_quality_surface_sha as f; print(f())"

If it's a pure refactor / logging / telemetry change, add the marker
[wiki-schema-no-bump] to a commit message in this PR.
EOF
exit 1
