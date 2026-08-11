# Document RAG

Code is not the only thing worth retrieving. Cograph runs **two separate
document-RAG paths** with their own discovery, parsers, chunkers, storage, job
models and access rules:

| | Repository documents | Markdown collections |
| --- | --- | --- |
| **Origin** | Discovered inside a repository checkout | Uploaded by an operator |
| **Belongs to** | One repository | Nothing — standalone |
| **Refreshed by** | Every sync, automatically | Explicit upload |
| **Parser** | Heading split | Full markdown parse (frontmatter, tables, code, links) |
| **Chunker** | Word window, 512 words / 64 overlap | Character budget, 4000 soft, atomic blocks |
| **Linked to code** | Yes — symbol mentions | No |
| **Cross-document links** | No | Yes — resolved within the collection |
| **Retrieval store** | `repo_docs` | `md_collections` |
| **Reachable via** | `cograph_retrieve(mode="wiki")` | `cograph_collection_search` |
| **UI** | `/repos/:host/:owner/:name/docs` | `/docs` |
| **Tables** | `repo_documents`, `repo_document_chunks`, `repo_document_chunk_mentions` | `md_collections`, `md_documents`, `md_chunks`, `md_links`, `md_jobs` |

They are not shared code, and the differences are deliberate. The division of
labour, as the agent playbook frames it: **code answers *how*; collections answer
*what* and *why*.**

---

## Repository documents

The repository's own documentation, indexed where it already lives.

### Discovery

The `index_repo_docs` pipeline step walks the checkout and classifies every file
it finds. Only three extensions are chunked as prose:

```
.md   .mdx   .rst
```

Other files are classified but not chunked — the classification is what gives a
document its **kind**:

| Kind | Matched by |
| --- | --- |
| `Repo Doc` | any `.md` / `.mdx` / `.rst` |
| `Example` | a path under `example`, `examples`, `sample`, `samples` with one of ~11 source extensions (`.go`, `.py`, `.ts`, `.tsx`, `.js`, `.json`, `.toml`, `.yaml`, `.yml`, `.md`, `.mdx`, `.rst`) |
| `Test` | a path under `test` / `tests` with a test-ish extension |
| `Config` | `.cfg`, `.conf`, `.env`, `.ini`, `.json`, `.toml`, `.yaml`, `.yml`, plus a set of root files: `Dockerfile`, `docker-compose.y*ml`, `compose.y*ml`, `go.mod`, `go.work`, `Makefile`, `package.json`, `package-lock.json`, `pyproject.toml` |
| `Workflow` | `.yml` / `.yaml` under a deploy-ish directory (`.devcontainer`, `deploy`, `deployment`, `helm`, `k8s`, `ops`) |

Excluded outright: hidden directories except `.devcontainer` and `.github`, and
an ignore list covering `node_modules`, `dist`, `build`, `venv`, `.venv`,
`__pycache__`, `.cograph`, and the usual tool caches (`.mypy_cache`,
`.pytest_cache`, `.ruff_cache`, `.tox`, `.idea`, `.vscode`, `.cache`).

### Chunking — word window with overlap

Sections are split at headings, then each section is windowed:

| Parameter | Value |
| --- | --- |
| `max_words` | 512 |
| `overlap_words` | 64 |

Each chunk carries its `heading_path` and `chunk_index`. Titles come from the
first `#` heading, falling back to the filename.

The 64-word overlap is the point: a sentence that straddles a window boundary
still appears whole in one of the two chunks, so a query matching it does not
fall between them.

### Indexing is idempotent

Documents are keyed by path and content hash. A sync reports six counters:

```
discovered_files  indexed_documents  indexed_chunks
unchanged_documents  deleted_documents  replaced_files
```

Unchanged documents cost nothing — no re-chunk, no re-embed. Deleted files have
their rows pruned. This is what makes hourly syncs affordable.

### Symbol linking — the part that makes these better than text search

After indexing, every chunk is scanned for identifiers in three forms:

| Pattern | Example |
| --- | --- |
| Backticked identifier | `` `ParseConfig` ``, `` `module.Class` `` |
| Plain dotted name | `service.handler.dispatch` |
| Function-call syntax | `validate()` |

Matches are resolved against `code_nodes.name` and `code_nodes.qualified_name`
for that repository, and the hits are stored in
`repo_document_chunk_mentions`.

Two features fall out of that table:

- **Code results carry their documentation.** A `cograph_retrieve` hit on a code
  node comes back with `related_repo_doc_chunks` — the doc chunks that discuss
  that symbol, heading-anchored, up to three by default.
- **Doc pages carry their sources.** The docs viewer shows the relevant code for
  the page you are reading.

Re-linking can be forced for unchanged documents, which is what you want after a
large rename: the prose did not change, but what it points at did.

### Slugs

Page URLs are derived deterministically:

1. strip a leading `docs/`, `doc/`, `documentation/` or `pages/`
2. drop the extension (`.md`, `.mdx`, `.markdown`, `.rst`, `.txt`)
3. lowercase
4. replace runs of non-alphanumerics with `-`
5. truncate to 80 characters

The function makes **no uniqueness guarantee** — two different paths can
normalise to the same slug — so the slug map appends a 4-hex-character suffix
derived from the document id on collision.

### Reading them

`/repos/:host/:owner/:name/docs` is a three-column reader: page tree, prose,
table of contents. It has sibling and previous/next navigation, and file-reference
pills that link through to the git host.

### Over MCP and REST

- `cograph_retrieve` with `mode="wiki"` searches this store. (Yes, the mode name
  is misleading — it searches **checked-in** markdown, not the generated wiki.)
- `GET /api/repos/{slug}/documents` and `/documents/{document_id}` for the
  indexed documents; `GET /api/repos/{slug}/docs` and `/docs/{slug}` for the
  rendered tree.

---

## Markdown collections

Curated corpora with no repository of their own: product requirements,
architecture decision records, glossaries, an exported internal wiki, runbooks,
on-call procedures.

### The parser

Collection documents get a full markdown parse — regex-based, no external
dependency — producing:

| Field | Contents |
| --- | --- |
| `frontmatter` | Parsed `---` YAML block. Supplies `title` when present. |
| `title` | Frontmatter `title`, else the first `#` heading |
| `heading_tree` | Every heading: `{level, text, anchor, line}` |
| `code_blocks` | Every fence: `{language, content, start_line, end_line}` |
| `tables` | Every pipe table: `{header, rows, start_line, end_line}` |
| `links` | Every link: `{text, href, line, link_type}` |
| `word_count`, `line_count` | Document size |

Anchors are generated GitHub-style: lowercase, punctuation stripped, whitespace
collapsed to `-`. That is what makes `collection/<id>#<heading>` a citable
address.

Links are classified into four types:

| `link_type` | Matched by |
| --- | --- |
| `absolute` | `http://`, `https://`, `mailto:`, `ftp://` |
| `anchor` | starts with `#` |
| `markdown` | ends with `.md` or `.mdx` |
| `relative` | anything else |

Both `[text](href)` and `[[wiki link]]` syntaxes are recognised — the latter
classified as `wiki`, so an exported wiki keeps its internal link graph.

### Chunking — character budget with atomic blocks

Splitting is heading-aware, and a chunk never splits a heading section. Three
parameters:

| Parameter | Value | Meaning |
| --- | --- | --- |
| `max_chars` | 4000 | **Soft** ceiling — exceeded only when a single atomic block is itself larger |
| `min_chars` | 400 | Floor for the trailing buffer |
| `max_chunks` | 512 | Hard cap per document |

Three invariants, and each exists because violating it produced a bad retrieval
result:

- **Tables are atomic.** Consecutive `|` rows are never cut. Half a table with no
  header is worse than no table.
- **Fenced code blocks are atomic.** Same reasoning — half a snippet is not
  runnable and not quotable.
- **No tail micro-chunks.** A trailing buffer under `min_chars` is merged into the
  previous chunk rather than emitted as a 40-character fragment that will match
  everything weakly.

When several sections merge into one chunk, the resulting `heading_path` is the
**deepest common prefix** of the merged paths, falling back to the first
section's path when they share nothing. Each chunk carries `heading_path`,
`heading_level`, `section_anchor` and `chunk_index`.

::: info Why two different chunkers
Repository docs are usually short technical pages where an overlap window
improves recall across a boundary. Collection documents are often long,
table-heavy specifications where preserving structure matters more than overlap.
Same problem, different corpus shape, different answer.
:::

### Ingestion

Documents are keyed by `source_key` — the path or name you upload them under —
and upserted by content hash. Each document result reports exactly one of three
states:

| State | Meaning |
| --- | --- |
| `created` | New `source_key` |
| `replaced` | Same key, different content hash — chunks rebuilt |
| `unchanged` | Same key, same hash — nothing done |

Batch uploads aggregate the counts. Re-uploading an unchanged corpus is free.

### Cross-document link resolution

After ingestion, a resolver walks the unresolved links and tries to bind each to
a document **within the same collection**, matching the href against either the
target's `source_key` or its normalised path. Resolved links get a
`target_document_id`; unresolvable ones stay unresolved rather than being
dropped, so a broken reference remains visible.

Cross-collection links are deliberately not resolved — a collection is the unit
of curation and of access control.

### Jobs

Embedding and link resolution are asynchronous. Three job kinds, four statuses:

| Kind | What it does |
| --- | --- |
| `upload` | Ingest a batch |
| `embed` | Vectorise chunks |
| `resolve_links` | Bind cross-document links |

| Status | |
| --- | --- |
| `queued` → `running` → `success` \| `error` |

Monitor at `/docs/jobs` (admin only). As with the main pipeline, jobs do not
auto-retry, so a worker killed mid-job would leave a row stuck at `running` — a
sweep clears stale ones. Failed jobs can be retried individually, and a whole
collection can be re-embedded, which is what you do after changing the embedding
model.

### Inspecting what retrieval actually sees

The document viewer has four tabs, and the fourth is the one to reach for when a
search is not finding something it should:

| Tab | Shows |
| --- | --- |
| Preview | Rendered markdown |
| Raw | The source text |
| Metadata | Frontmatter, counts, parsed structure |
| **Chunks** | **The exact units that were embedded, with their heading paths** |

If a fact lives in the middle of a 6000-character section, the Chunks tab tells
you which chunk carries it — and whether it got merged somewhere unhelpful.

### Search

Collection search runs the same hybrid retriever as code, minus the symbol
stream (there are no qualified names to match): dense vector plus PostgreSQL
full-text using the **`english`** configuration — correct here, unlike the code
store, because this is prose.

Listing endpoints use `ILIKE` on names and descriptions; that is a filter, not
retrieval.

### Visibility

Collections have their own three-value model, separate from repository
visibility:

| Value | Effect |
| --- | --- |
| `public` | Readable by anyone who can read the deployment |
| `private` | Owner, or a group grant |
| `admin_only` | Owner, or a group grant |

::: warning `private` and `admin_only` behave identically
Neither appears in the public read funnel, and both fall through to
owner-or-grant. One of the two values carries no distinct behaviour today.

Note also that collections **leak existence**: an unreadable collection returns
`403` while a missing one returns `404`. Repositories return `404` for both.
:::

### Over MCP and REST

Four of the fourteen MCP tools:

| Tool | Purpose |
| --- | --- |
| `cograph_collections` | List readable collections |
| `cograph_collection_search` | Hybrid search inside one collection |
| `cograph_collection_document` | One full document plus parsed metadata, untruncated |
| `cograph_read_chunk` | One chunk in full |

Collections are also first-class in `cograph_route`, scored in their own pool —
and always returned up to the requested count even at low confidence, because a
glossary frequently answers a question whose vocabulary does not appear in it.

REST lives under `/api/md-collections`: CRUD, single and batch document upload,
document read and delete, chunk listing, `search`, `embed-status`, `re-embed`,
per-collection and global job lists, and job retry.

---

## Which one should I use?

**Repository documents** when the text belongs to the code — READMEs,
architecture notes, in-tree ADRs. It refreshes itself on every sync and links to
symbols, and you get both for free.

**A collection** when the text has no natural repository — cross-service
requirements, a domain glossary, an internal wiki export, runbooks. You get
cross-document link resolution and standalone access control.

**Do not do both for the same document.** You would pay to embed it twice, and
the two copies would compete in retrieval while drifting apart.

## Tuning notes

None of the chunker parameters are exposed as configuration — they are
constructor defaults. If you need to change them, that is a code change, and the
consequence is that existing chunks were built with the old values: re-embed the
affected corpus afterwards, or retrieval quality will be inconsistent across
documents.

The one knob that *is* configurable and matters here is
`COGRAPH_EMBEDDING__BATCH_SIZE` (default 256), which bounds how many chunks go
to the provider per request.
