"""The `instructions=` text every MCP client gets at `initialize`.

Clients show only the first part of it, so it stays short: what Cograph
is, when to use it, and how to find, read and stop. Per-tool rules live
in the tool descriptions. Tests pin its size.
"""

INSTRUCTIONS = """\
Cograph is a read-only index of your team's repositories and docs: search code by meaning or name, read exact source, find callers, search docs. Repositories outside your working directory are visible only through Cograph.

## When to use it
- A repository that is not in your working directory.
- A search by meaning, when you do not know a name.
- Team docs: what a term means, why something was decided.
If the repository is in your working directory and you know a name, grep and read the files yourself: it is cheaper, and your files may be newer than the index. Cograph knows code and docs as of its last index, not live systems or data.

## How to answer
1. Find. Tools take a repository as host/owner/name. Known only by name: cograph_repositories(search=name). Unknown: cograph_route. A word from the code: cograph_search_code. A question in words: cograph_retrieve, one topic per call.
2. Read. Search results show an excerpt, not the whole code. Read the best one or two hits with cograph_read_node(node_id). If the question is how something is set or used and the hit is only a helper, read its callers; if it is a test, read what it calls: cograph_related with direction=callers or callees.
3. Stop. Answer once you have read the code or doc that answers the question and can cite it. Most questions need 2-4 calls. Do not repeat a call whose result you still have. If two differently worded searches find nothing relevant, say what you searched and stop.

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
