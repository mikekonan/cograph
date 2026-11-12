"""The `instructions=` text every MCP client gets at `initialize`.

Clients show only the first part of it, so it stays short: what Cograph
is, when to use it, and how to find, read and stop. Per-tool rules live
in the tool descriptions. Tests pin its size.
"""

INSTRUCTIONS = """\
Cograph is a read-only index of your team's repositories and docs: search code by meaning or name, read exact source, find callers, search docs. Repositories outside your working directory are visible only through Cograph.

## When to use it
- A repository not in your working directory.
- A search by meaning, without a name.
- Team docs: what a term means, why something was decided.
If the repository is in your working directory and you know a name, grep and read the files yourself: it is cheaper, and they may be newer than the index. Cograph knows code and docs as of its last index, not live systems or data.

## How to answer
1. Find. Tools take a repository as host/owner/name. Known only by name: cograph_repositories(search=name). Unknown: cograph_route. A word from the code: cograph_search_code. A question in words: cograph_retrieve, one topic per call.
2. Read. Results show an excerpt: read the best one or two hits with cograph_read_node(node_id). A doc hit has no node_id: read its `at` lines with cograph_read_file_range. A helper: read its callers; a test: what it calls (cograph_related, direction=callers or callees). For how or why something works, read the page a result's `wiki` names (its `section`) with cograph_wiki_page.
3. Check. For what the code does at run time, read where its input comes from, its callers in product code (not `kind` test), and the layer above. A branch is not a case that happens until you show a caller that reaches it. A value sent to another service (event, message, RPC) is decided there: check that service before you conclude; a search's `elsewhere` names it. What you could not show, mark "not verified".
4. Stop. Answer once you can cite the code or doc that answers the question. Do not repeat a call whose result you still have. If two differently worded searches find nothing relevant, say what you searched and stop.

## Citations
Cite each claim. Code: path:start-end, as given in the result. Docs and wiki: title and heading.
"""

# Served by the `cograph://briefing` resource when no operator text is saved.
DEFAULT_BRIEFING = """\
This Cograph deployment hasn't been customised yet.

Cite every claim with `file_path:start_line-end_line` or `wiki/<slug>`
as given in the result. If you cannot cite, the correct
answer is "I don't have enough information in this Cograph instance to
answer."

If the user asks domain-specific questions ("what does <acronym>
mean", "which service owns <feature>"), tell them to ask their
Cograph admin to fill in the operator briefing at /admin?tab=mcp.
"""
