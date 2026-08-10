# Why Cograph

## The problem

Every team past a certain size has the same two unsolved documentation problems,
and they are the same problem wearing different clothes.

**A codebase is too large for a prompt.** A repository with a few hundred
thousand lines does not fit in any context window, and the naive workaround —
pasting the files you *think* are relevant — fails precisely when you do not
already know where the answer lives. That is the only time you needed help.

**Hand-written documentation drifts.** A wiki page describing a subsystem is
accurate on the day it is written. Six months later nobody knows which
paragraphs still hold, so nobody trusts any of them, so nobody updates them.

Both leave the same questions unanswered:

- Where is this behaviour implemented?
- What calls this function, and what does it depend on?
- Which files explain this subsystem?
- What changed since the last time I looked?
- What source evidence supports this answer?

Cograph answers those from the repository itself, with a citation attached to
every claim, and re-derives the answers on every sync so they cannot go stale
without someone noticing.

## What Cograph does

1. Clones or updates a Git repository.
2. Parses supported source files with tree-sitter into modules, symbols, and
   call/import/inheritance edges.
3. Indexes in-tree markdown, generates AST summaries, and embeds both code and
   prose.
4. Generates a wiki whose every page must cite evidence the writer verified.
5. Serves the result to people through a web UI and to agents through MCP.

The whole thing runs on your infrastructure against one PostgreSQL database.

## How it differs

Cograph is not the only way to attack this. Here is where it deliberately
diverges, and why.

### Instead of pasting files into a chat

The unit of retrieval is a bounded, cited excerpt, not a file. Responses are
built to a token budget — snippets are centred on the matched terms and clipped
to a configurable width, and every result names the file and line range it came
from. An agent gets `path:120-168` plus 600 characters of the right function,
not 4,000 lines of the wrong module.

The practical consequence is that the answer stays auditable. When a coding
agent tells you a retry lives in a particular function, you can check.

### Instead of a hand-written wiki

Wiki pages are generated from the indexed snapshot, and regenerated when the
code they cite changes. Drift is one sync away rather than one review cycle
away.

The interesting design choice is what happens when the model *cannot* ground a
claim. Cograph does not let it write the paragraph anyway: a page must answer
each question it promised to cover with a marked, cited section, and an
un-groundable question is dropped from the page rather than answered vaguely.
The page ships marked as partial. See [the wiki page](/wiki) for the mechanics.

### Instead of embedding-only RAG

Vector search alone is bad at the queries developers actually type. `ParseConfig`
is an exact symbol; `func (s *Server) Handle` is a signature; `retry with
backoff` is a concept. One index cannot be good at all three.

So Cograph runs three retrieval streams — dense vector, Postgres full-text, and
fuzzy symbol matching over qualified names — and fuses their rankings. The code
store's full-text index deliberately uses Postgres's `simple` configuration
rather than `english`, because English stemming mangles identifiers. Structure
comes from parsers and SQL queries, not from asking a model to guess at call
relationships.

[Retrieval internals](/retrieval) has the full picture.

### Instead of a hosted code-intelligence service

Cograph is self-hosted, and the boundary is drawn deliberately: repository
contents, the extracted graph, embeddings, generated pages and access control
all live in your database. The only thing that leaves is the text you send to
whichever OpenAI-compatible endpoint you configure — which can be your own.

LLM credentials are configured server-side, so browsers and agents never hold a
provider key. An agent authenticates to Cograph with a scoped personal access
token and Cograph talks to the model on its behalf.

### Instead of adding a graph database

A code graph is the obvious use case for a graph database, and Cograph does not
use one. PostgreSQL is the single system of record: graph nodes and edges,
source files, document chunks, generated pages, `pgvector` embeddings, full-text
and `pg_trgm` indexes, plus ordinary application state — users, jobs, audit
rows — in one place with one backup story and one transaction boundary.

The trade is real: some traversals are more awkward in SQL than in Cypher. What
it buys is that a single-node install is genuinely a single node, ingest and
application state commit together, and there is no second datastore to keep
consistent. For a self-hosted tool, that is the right side of the trade.

## Who it is for

- **A developer** landing in an unfamiliar service who needs the shape of it
  before touching anything.
- **A coding agent** that needs specific, cited context and a way to widen the
  search when the first attempt misses.
- **A platform or DX team** that wants a code knowledge layer over many
  repositories without shipping source to a third party.
- **A team with a documentation deficit** that would rather generate a
  defensible baseline than schedule a documentation sprint that will not happen.

## Where to go next

| If you want to… | Read |
| --- | --- |
| Understand the vocabulary | [Concepts](/concepts) |
| See how the pieces fit | [Architecture](/architecture) |
| Know what is actually supported | [Supported languages](/languages) |
| Run it locally | [Quickstart](/quickstart) |
| Run it on Kubernetes | [Kubernetes](/install/kubernetes) |
| Connect Claude, Cursor or Codex | [MCP server](/mcp) |
| Know the limitations before committing | [FAQ](/faq) |
