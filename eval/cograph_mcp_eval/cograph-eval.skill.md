---
name: cograph-eval
description: Run the Cograph MCP eval set (baseline or after) against the connected Cograph MCP server. Records tool calls, answers, and metrics to eval/results/<target>.jsonl.
---

# /cograph-eval <target>

You are the eval agent. The user has invoked `/cograph-eval baseline` or
`/cograph-eval after`. The argument is the **target name** (`baseline`,
`after`, or any custom label) for the resulting JSONL file.

## Preconditions

1. The Cograph MCP server must be connected to your tool surface. Tools
   prefixed with `cograph.` (or `mcp__cograph__*`) must be visible. If
   they are not, **stop and tell the user** to run `cograph-connect setup`
   first — do NOT silently fall back to filesystem grep.
2. Read `/Users/enquix/work/cograph/eval/cograph_mcp_eval/questions.yaml`.
   It contains ~40 questions with their expected keywords and provenance.
3. Confirm the target argument with the user if it's missing.

## Eval rules

For each question in `questions.yaml`:

1. Treat it as a fresh conversation. Do not let earlier questions in
   the same run influence the strategy beyond what cograph-connect
   SKILL.md prescribes.
2. **Use only Cograph MCP tools** (or REST `/api/...` if MCP exposes
   that). Do NOT read local files via `Read`/`Bash`. Do NOT search via
   `Grep`/`Explore`. Do NOT fetch from the web. The whole point of
   the eval is to measure what the agent extracts from Cograph, not
   what it can pull from your environment.
3. For each tool call, record:
   - `name` — the exact tool name as called.
   - `args` — the arguments you passed (omit secrets).
   - `result_bytes` — `len(result_as_string)` (best-effort).
   - `took_ms` — best-effort wall-time. If unknown, omit.
4. After deciding the answer, write the answer in 1-3 sentences (or
   verbatim content for "give me X verbatim" questions).
5. Extract `citations_seen`: every `file_path:line_range` or `wiki/<slug>`
   string mentioned in your answer. Use the literal substring.
6. Append the record to the JSONL by invoking:

   ```bash
   python -m eval.cograph_mcp_eval.record \
       --target=<target> \
       --question-id=<id> \
       --record-json='{"tool_calls": [...], "answer": "...", "citations_seen": [...]}'
   ```

   If the JSON is too long for a CLI arg (likely!), pipe via stdin:

   ```bash
   cat <<'JSON' | python -m eval.cograph_mcp_eval.record \
       --target=<target> --question-id=<id>
   {"tool_calls": [...], "answer": "...", "citations_seen": [...]}
   JSON
   ```

7. Negative questions (`negative: true`): the *correct* answer is a
   graceful "I don't have enough information" / "no such symbol" /
   "out of scope". Do NOT hallucinate. Do NOT fall back to web.
8. If a question genuinely cannot be answered with the current MCP
   surface (e.g. no `outline` tool exists yet at baseline), still
   attempt with whatever tool comes closest, record what you actually
   did, and let `score.py` decide.

## Cap

If a single question exceeds **8 tool calls**, stop, record what you have
with `"capped": true` in the record, and move on. Don't dig forever.

## After the run

Once every question has a record:

1. Score:
   ```bash
   python -m eval.cograph_mcp_eval.score --target=<target>
   ```
   This emits `eval/results/<target>.summary.json`.

2. If both `baseline.summary.json` and `after.summary.json` exist, run
   the comparator:
   ```bash
   python -m eval.cograph_mcp_eval.compare \
       --baseline=eval/results/baseline.summary.json \
       --after=eval/results/after.summary.json
   ```
   Print the resulting table to the user. If any row is `❌`, hold the
   merge.

## Output

Tell the user where the JSONL + summary live, and paste the summary
JSON as the final message. No emoji unless the user asked.
