# Reading these docs with an agent

This site publishes itself as plain markdown so an agent can read the
documentation without scraping HTML. Three artefacts are regenerated on every
build, so none of them can describe a version of the site that no longer exists.

| URL | What it is | Size |
| --- | --- | --- |
| [`/llms.txt`](https://cograph.cc/llms.txt) | Index: every page with a one-line description, following [llmstxt.org](https://llmstxt.org) | ~3 KB |
| [`/llms-full.txt`](https://cograph.cc/llms-full.txt) | Every page concatenated into one file | ~210 KB |
| `/<page>.md` | The markdown source of a single page — for example [`/retrieval.md`](https://cograph.cc/retrieval.md) | 2–30 KB |

## Which one to fetch

Start with `/llms.txt` and follow the one or two pages the question needs. The
descriptions exist precisely so that choice can be made from a 3 KB read
instead of a 210 KB one.

Fetch `/llms-full.txt` when the agent has a large context window and the
question is genuinely cross-cutting — "how does a query become an answer" spans
`retrieval`, `mcp` and `modes`. It is one request and roughly 55k tokens.

The generated references are the two pages worth reading in full rather than
searching: [`/mcp-reference.md`](https://cograph.cc/mcp-reference.md) is the
complete MCP tool surface with every parameter and bound, and
[`/api-reference.md`](https://cograph.cc/api-reference.md) is every REST
endpoint. Both are generated from the running server, not written by hand, and
CI fails when they drift from the code.

## This is not the MCP server

These files describe **Cograph itself** — they let an agent answer questions
about what Cograph is and how to run it.

Pointing an agent at *your own* indexed repositories is a different thing: that
is Cograph's [MCP server](/mcp), which serves a generated wiki, hybrid
retrieval and a code graph over 14 tools, against the code you indexed. This
page will not help an agent read your code; `/mcp` will.

## Staying current

Every artefact here is built from the same sources as the rendered site, in the
same job, so a page and its markdown copy can never disagree. There are no
snapshots and no versioned copies: the site documents one release at a time,
named in the footer. An agent that cached `/llms-full.txt` should re-fetch it
rather than trust the copy — pre-1.0, the answer to "how do I configure this"
does still move between releases.
