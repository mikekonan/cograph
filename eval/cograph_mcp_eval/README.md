# Cograph MCP eval

A frozen question set + recorder + scorer + comparator for measuring how
well an MCP-aware agent extracts information from the Cograph server.

## What it measures

| # | Claim | Metric | Target |
|---|-------|--------|--------|
| H1 | Fewer MCP calls per question | median `tool_calls_count` | ≥ 30% lower vs baseline |
| H2 | Less context burnt | median `tokens_estimate` (= bytes/4) | ≥ 40% lower |
| H3 | Source-anchored answers | `cites_provenance_rate` | +≥10pp and ≥80% absolute |
| H4 | No correctness regression | `correctness_rate` | within ±2pp of baseline |
| H5 | Truncation doesn't hide answers | `incorrect_rate` on positive Qs | ≤ baseline |
| H7 | Agent does not bail early | `too_early_giveup_rate` | 0 |

H7 fires when the agent says it found nothing ("I don't have enough
information", "could not find", "no matches", …) on a **positive**
question with `tool_calls_count < 2`. Two different empty attempts are
enough to stop; fewer is giving up too early. `compare.py` uses
`direction: must_be_zero` for this row, so any non-zero rate on `after`
holds the merge.

A **negative** question is correct when the answer holds any of its
keywords or any give-up phrase. A refusal has many wordings.

H1 and H2 count every tool call and every result byte, client tools
(file reads, grep) included. There is no separate penalty for reading
local files: the eval measures what an answer costs, whichever tool
paid for it.

## Files

- `questions.yaml` — frozen 40-question set. Eight categories:
  `repo_overview`, `symbol_lookup`, `feature_flow`, `wiki_grounded`,
  `file_range`, `collection_navigation`, `negative`, `composite`.
- `record.py` — appends one JSONL line per question. Called from inside
  the `/cograph-eval` skill loop.
- `score.py` — reads the JSONL, applies deterministic scoring rules
  (no LLM-as-judge), emits `<target>.summary.json`.
- `compare.py` — diffs `baseline.summary.json` against `after.summary.json`
  per metric and prints a pass/fail table.
- `cograph-eval.skill.md` — the slash command body Claude reads when
  you invoke `/cograph-eval`. Symlink it into your local
  `.claude/commands/` (which is gitignored as user-specific) to
  enable the skill:

  ```bash
  mkdir -p .claude/commands
  ln -s ../../eval/cograph_mcp_eval/cograph-eval.skill.md \
        .claude/commands/cograph-eval.md
  ```

- `live_run.py` - runs a real agent (`claude -p`) once per question, run
  and text variant, and prints a per-question table. See "Live run".
- `proxy.py` - stdio MCP proxy the live run puts between the agent and the
  server, to swap instructions and tool descriptions.
- `live_questions.yaml` - six live-run questions on open repositories.

## Live run

The skill flow above trusts the agent to report its own calls. The live run
does not: each answer comes from a fresh `claude -p` process, and calls,
result bytes and the answer are read from its event stream. It is the gate
for a change to the server instructions or tool descriptions.

```bash
# The backend venv has the mcp package the proxy needs.
COGRAPH_MCP_URL=http://localhost:8000/mcp COGRAPH_MCP_TOKEN=<PAT> \
backend/.venv/bin/python -m eval.cograph_mcp_eval.live_run \
    --variant old --variant new=path/to/new.yaml --runs 3 --out /tmp/live
```

- **Variants.** `old` without a file keeps the server's own text. A variant
  file is YAML with `instructions` and/or `tools: {<name>: {description: …}}`.
  Both variants run against the same server build, so only the text differs.
  A tool name the server does not have stops the run.
- **Isolation.** The agent starts with `--setting-sources ""` (no plugins,
  hooks or CLAUDE.md) and only `Read`, `Grep`, `Glob` and read-only `Bash`
  besides the Cograph tools. The token reaches the proxy through the
  environment, never a file.
- **Table.** One row per question and variant: correct answers, runs that
  `reached` the answer (one tool result held both `expected_location` strings),
  runs that cited the expected file, median calls and KB, calls per tool.
  Compare rows, not averages: a new text can help one question and break another.
- **Questions.** `live_questions.yaml` needs `github.com/mikekonan/cograph`,
  `github.com/samber/lo` and `github.com/samber/mo` indexed. `cwd` sets the
  agent's working directory, so a question can have the repository on disk or
  not. Pass your own set with `--questions`; keep a private set and its
  `--out` outside this repository.
- **Cost.** Real model tokens: one agent session per question × run × variant.
  `--claude-args "--model <name>"` picks the model.

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

# Then locally, from the repository root:
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
(`negative: true`) to catch H5 regressions.

## Re-running a single category

There is no category filter. The skill re-runs everything, so to redo one
category: delete its lines from `<target>.jsonl` and re-invoke `/cograph-eval`
for the same target. `score.py` is last-write-wins on duplicate question IDs, so
the re-run replaces the deleted records.
