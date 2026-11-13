"""The `instructions=` text every MCP client gets when it connects.

Clients show only about the first 2000 characters, so the whole text fits in
that: a short general playbook (what Cograph is, how to find, read, check and
stop), then the operator briefing from `/admin?tab=mcp` under
`## This deployment`. A briefing too long to fit is not sent at all, so agents
never get a cut-off one. Per-tool rules live in the tool descriptions.
"""

INSTRUCTIONS = """\
Cograph is a read-only index of your team's repositories and docs. Repositories outside your working directory are visible only through Cograph; one in your working directory you grep and read yourself. Cograph knows code and docs as of its last index, not live data.

## How to answer
1. Find. A repository is host/owner/name; by name: cograph_repositories(search=name); unknown: cograph_route. A word from the code: cograph_search_code. A question in words: cograph_retrieve, one topic per call.
2. Read the best one or two hits with cograph_read_node(node_id); a doc hit: its `at` lines with cograph_read_file_range. Callers or callees: cograph_related. For how or why: the page a result's `wiki` names, with cograph_wiki_page.
3. Check. For run-time behaviour read where the input comes from, its product callers (not `kind` test) and the layer above. A branch happens only if a product caller reaches it; if none can, say it does not happen. A value sent to another service or set in config means what the code reading it does: read that code; `elsewhere` names it. What you could not show, mark "not verified".
4. Stop once you can cite the answer. Do not repeat a call. Two searches with nothing: say what you searched and stop.
Cite each claim: path:start-end; docs: title and heading.
"""

# Clients cut longer instructions.
INSTRUCTIONS_LIMIT = 2000

_BRIEFING_HEADING = "\n## This deployment\n"

# What the playbook, the heading and the closing newline leave of the budget.
BRIEFING_LIMIT = INSTRUCTIONS_LIMIT - len(INSTRUCTIONS) - len(_BRIEFING_HEADING) - 1


def compose_instructions(briefing: str) -> str:
    """The playbook plus the operator briefing, or the playbook alone.

    An empty briefing, or one over `BRIEFING_LIMIT`, gives the playbook alone.
    """

    briefing = briefing.strip()
    if not briefing or len(briefing) > BRIEFING_LIMIT:
        return INSTRUCTIONS
    return f"{INSTRUCTIONS}{_BRIEFING_HEADING}{briefing}\n"
