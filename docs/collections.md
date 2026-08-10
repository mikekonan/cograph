# Docs & collections

Cograph indexes prose from two sources, and they are entirely separate
subsystems — separate tables, ingestion paths, chunkers and access models. The
distinction matters because it decides where you *put* a document.

| | Repository documents | Markdown collections |
| --- | --- | --- |
| **Origin** | Discovered inside a repository checkout | Uploaded by an operator |
| **Tied to a repository** | Yes | No |
| **Updated by** | Every sync | Explicit upload |
| **UI** | `/repos/:host/:owner/:name/docs` | `/docs` |
| **Linked to code** | Yes — symbol mentions | No |
| **Retrieval layer** | `repo_doc` | Collection search only |
| **Chunking** | Word window with overlap | Character budget, structure-preserving |

The division of labour, as the agent playbook frames it: **code answers *how*;
collections answer *what* and *why*.**

## Repository documents

The repository's own documentation, found where it already lives.

### Discovery

During the `index_repo_docs` step, the checkout is walked for `.md`, `.mdx` and
`.rst` files. Other extensions are classified but not chunked as prose — workflow
YAML, example sources in eleven languages, tests, and root config files like
`Dockerfile`, `go.mod`, `Makefile` and `compose.yaml` — which feeds the document
*kind* classification.

Conventional noise is skipped, and hidden directories are excluded apart from
`.devcontainer` and `.github`.

### Chunking

Documents are split at headings, then long sections are windowed at **512 words
with 64 words of overlap**. Each chunk keeps its `heading_path` and index, so a
result can be cited as a specific section rather than a whole file. Titles come
from the first `#` heading, falling back to the filename.

### Indexing is idempotent

Content-hash based. A sync reports discovered files, indexed documents, indexed
chunks, unchanged documents, deleted documents and replaced files — so a re-sync
of unchanged docs costs nothing.

### Symbol linking

This is what makes repository documents more useful than generic text search.
After indexing, chunks are scanned for identifiers in three forms — backticked
names, plain dotted names, and `func()` call syntax — and matched against code
node names and qualified names.

The resulting mentions power two things: a code result can carry the doc chunks
that discuss it (`related_repo_doc_chunks`), and a doc page can show its relevant
sources.

### Slugs

URLs are derived deterministically: strip a leading `docs/`, `doc/`,
`documentation/` or `pages/`, drop the extension, lowercase, replace non-alphanumerics
with hyphens, truncate to 80 characters. The function makes no uniqueness
guarantee, so collisions get a short suffix from the document id.

### Browsing

The docs view is a three-column read — page tree, prose, table of contents — with
sibling and previous/next navigation, and file-reference pills that link through
to the git host.

## Markdown collections

Curated corpora that do not live in any repository: product requirements,
architecture decision records, glossaries, a mirrored internal wiki, runbooks.

### Creating and filling one

Create a collection at `/docs`, choose its visibility, then upload documents
individually or as a batch. Parsing is regex-based with no external dependency and
extracts frontmatter, the heading tree with anchors, code blocks, tables, links
(both `[]()` and `[[wiki]]` forms), and word/line counts.

### Chunking, and why it differs

Collections chunk by **character budget** — 4000 soft maximum, 400 minimum, up to
512 chunks per document — with two hard invariants:

- **Tables are atomic.** Consecutive `|` rows are never split, because half a table
  is worse than no table.
- **Fenced code blocks are atomic.** Same reason.

A trailing fragment below the minimum merges into the previous chunk, and merged
sections take the deepest common heading path.

::: info Two chunkers on purpose
Repository docs use words with overlap; collections use characters with structural
invariants. They are not shared code. Repository docs are usually short technical
pages where overlap helps recall across a boundary; collection documents are often
long, table-heavy specifications where preserving structure matters more.
:::

### Inspecting what retrieval sees

The document view has four tabs: **Preview**, **Raw**, **Metadata**, and
**Chunks**. The Chunks tab is the useful one when a search is not finding
something — it shows the exact units that were embedded, with their heading paths.

### Cross-document links

After ingestion, a resolver matches unresolved link targets against other
documents in the **same** collection, by source key or normalised path. Links
resolve as `wiki`, `markdown` or `absolute`.

### Jobs

Embedding and link resolution run asynchronously. Monitor them at `/docs/jobs`
(admin only): kinds are `embed`, `resolve_links` and `upload`, with statuses
`queued`, `running`, `success`, `error`.

Like the main pipeline, these jobs do not auto-retry, so a crashed worker would
leave a job `running` forever — a sweep clears stale ones. Failed jobs can be
retried, and a whole collection can be re-embedded, which is what you want after
changing the embedding model.

### Visibility

Collections have their own three-value model: `private`, `public`, `admin_only`.
Access is public, owner, or via a group grant.

::: warning `private` and `admin_only` behave identically
In the current read-scope implementation neither appears in the public funnel and
both fall through to owner-or-grant. One of the two values carries no distinct
semantics today.
:::

## Over MCP

Four of the fourteen tools cover collections:

| Tool | Purpose |
| --- | --- |
| `cograph_collections` | List readable collections |
| `cograph_collection_search` | Hybrid search inside one collection |
| `cograph_collection_document` | One full document, untruncated |
| `cograph_read_chunk` | One chunk in full |

Collections are also first-class in `cograph_route`, scored in their own pool
alongside repositories — and always returned up to the requested count even at low
confidence, because a glossary frequently answers a question whose vocabulary does
not appear in it.

## Which should I use?

**Repository documents** when the text belongs to the code: READMEs, architecture
notes, ADRs kept in-tree. It updates itself on every sync and links to symbols.

**A collection** when the text has no natural repository: cross-service product
requirements, a domain glossary, an internal wiki export, on-call runbooks.

If a document exists in a repository, do not also upload it as a collection. You
would pay to embed it twice and both copies would compete in retrieval.
