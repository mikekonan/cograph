# Cograph MCP eval

A frozen question set + recorder + scorer + comparator for measuring how
well an MCP-aware agent extracts information from the Cograph server.

## What it measures (H1-H6)

| # | Claim | Metric | Target |
|---|-------|--------|--------|
| H1 | Fewer MCP calls per question | median `tool_calls_count` | ≥ 30% lower vs baseline |
| H2 | Less context burnt | median `tokens_estimate` (= bytes/4) | ≥ 40% lower |
| H3 | Source-anchored answers | `cites_provenance_rate` | +≥10pp and ≥80% absolute |
| H4 | No correctness regression | `correctness_rate` | within ±2pp of baseline |
| H5 | Truncation doesn't hide answers | `incorrect_rate` on positive Qs | ≤ baseline |
| H6 | No silent grep/web fallback | `silent_fallback_rate` | 0 |

## Files

- `questions.yaml` — frozen 40-question set. Eight categories:
  `repo_overview`, `symbol_lookup`, `feature_flow`, `wiki_grounded`,
  `file_range`, `collection_navigation`, `negative`, `composite`.
- `record.py` — appends one JSONL line per question. Called from inside
  the `/cograph-eval` skill loop.
- `score.py` — reads the JSONL, applies deterministic scoring rules
  (no LLM-as-judge), emits `<target>.summary.json`.
- `compare.py` — diffs `baseline.summary.json` against `after.summary.json`
  per H1-H6 and prints a pass/fail table.
- `cograph-eval.skill.md` — the slash command body Claude reads when
  you invoke `/cograph-eval`. Symlink it into your local
  `.claude/commands/` (which is gitignored as user-specific) to
  enable the skill:

  ```bash
  mkdir -p .claude/commands
  ln -s ../../eval/cograph_mcp_eval/cograph-eval.skill.md \
        .claude/commands/cograph-eval.md
  ```

## How to run

### Preconditions

- Cograph stack running locally (`docker compose up`).
- Test instance has these repos indexed:
  - `github.com/mikekonan/cograph` (this repo)
  - `github.com/samber/lo`
  - `github.com/samber/mo`
  - `github.com/mikekonan/go-oas3`
- One md_collection named `cograph-design-notes` populated with a few
  short markdown docs covering auth, retrieval, and design tokens. (Use
  whatever's already there — the questions are forgiving on exact
  contents, only the topic must be roughly right.)
- A PAT with scopes `mcp` and `api:read`.
- `cograph-connect setup` has been run for your Claude session so the
  `cograph.*` MCP tools are visible.

### Baseline

```bash
# In Claude (Code or Desktop) with cograph-connect attached:
/cograph-eval baseline

# Then locally:
cd /Users/enquix/work/cograph
python -m eval.cograph_mcp_eval.score --target=baseline
git add eval/results/baseline.jsonl eval/results/baseline.summary.json
git commit -m "eval: capture baseline metrics for MCP eval"
```

### After the MCP refactor lands

```bash
/cograph-eval after
python -m eval.cograph_mcp_eval.score --target=after
python -m eval.cograph_mcp_eval.compare \
    --baseline=eval/results/baseline.summary.json \
    --after=eval/results/after.summary.json
```

If every row reads ✅, ship the PR. If any reads ❌, hold and revisit.

## Cost

$0. Claude (this session or Claude Desktop) is the eval agent. No
external LLM API.

Wall-clock: ~10-20 min per run.

## Why no LLM-as-judge

`score.py` is deterministic — exact-substring matching on
`expected_answer_keywords`. Strictly weaker than a judge, but reproducible
across re-runs, and we'd be the judge anyway (the agent is Claude, the
judge would also be Claude — same homework, same grader).

If `score.py` flags a question as `incorrect` but the answer is
clearly fine on inspection, fix the keyword list in `questions.yaml`
rather than working around it. The eval set is meant to evolve.

## Adding questions

`questions.yaml` is meant to grow. Categories are buckets; aggregations
are reported per-category in the summary. Keep the set under ~80 to
keep wall-clock manageable. Always include negative questions
(target=true) to catch H5/H6 regressions.

## Re-running a single category

The skill currently re-runs everything. To re-run one category:

```bash
# Trim baseline.jsonl to the records you want to keep, then run:
/cograph-eval baseline-rerun-symbol-lookup
# (the skill should accept a category filter — TODO if needed)
```

For now, just delete the relevant lines from the JSONL and re-invoke
`/cograph-eval` for the same target — `score.py` last-write-wins on
duplicate IDs.
