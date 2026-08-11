# Supported languages

A file can be picked up by four *independent* mechanisms, and they cover
different sets. Read this table before assuming a language is "supported" —
"supported" means different things depending on what you want to do.

| Mechanism | Coverage | Gives you |
| --- | --- | --- |
| **Graph extraction** | Python, Go, TypeScript, JavaScript — **only** | Symbols, call/import/inheritance edges, code search, `cograph_read_node`, graph browsing |
| **Source-range reads** | The same four languages | `cograph_read_file_range`, and the file bodies retrieval quotes from |
| **Repository documents** | `.md` / `.mdx` / `.rst` anywhere, plus a specific set of workflow, example, test and root-config files | Chunked, embedded, searchable prose; symbol links back to code |
| **Language statistics** | Many languages (the whole checkout is scanned) | The composition chart on the repository Overview |

::: danger A file in an unlisted language is not indexed at all
This is the most important limitation on this page. Source discovery skips any
file whose extension is not one of the four graph languages, so a `.rs`, `.java`,
`.rb` or `.php` file:

- has **no** symbols and appears nowhere in the graph,
- is **not** searchable — it is never chunked or embedded,
- and returns `NOT_FOUND` from `cograph_read_file_range`, because that tool reads
  the source-file table rather than the checkout.

The only traces such a file leaves are its bytes in the language-statistics
chart, and its contents *if* it happens to match the repository-document rules
below (for example a root `Dockerfile` or `pyproject.toml`).
:::

## Graph extraction

| Language | Extensions | tree-sitter grammar |
| --- | --- | --- |
| Python | `.py`, `.pyi` | `python` |
| Go | `.go` | `go` |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` | `typescript`, plus `tsx` for `.tsx` |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | `javascript` |

TypeScript uses two grammars on purpose: the `typescript` grammar cannot parse
JSX, and the `tsx` grammar mis-parses legacy `<T>expr` type assertions. Files are
routed by extension, and the parser cache is keyed by grammar rather than by
language.

TypeScript and JavaScript share one walker, since the TS grammars are supersets
of JavaScript.

::: tip Grammars are baked into the image
tree-sitter grammars are downloaded at Docker build time, not at runtime. A
runtime download once stalled a production sync for ten minutes inside an open
database transaction. The worker verifies grammar availability at startup and
fails loudly if any are missing.
:::

## What gets extracted

### Node kinds

Ten: `module`, `class`, `struct`, `interface`, `function`, `method`, `variable`,
`constant`, `type_alias`, `attribute`.

### Edge kinds

Exactly four: `declares`, `imports`, `inherits`, `calls`.

::: warning There is no `implements` edge
Interface satisfaction collapses into `inherits`. Go struct embedding, Go
interface embedding and TypeScript's `implements` clause all produce `inherits`
edges — TypeScript's `extends` and `implements` are gathered into the same list
before emission. If you are querying the graph for "who implements this
interface", search `inherits` and expect both relationships.

Note also that graph *traversal* labels every returned edge as `calls`,
regardless of the underlying row. Traversal is a caller/callee tool; use the node
detail endpoints when the edge kind matters.
:::

### Per-language capability matrix

The walkers are not identical. Where they diverge:

| Capability | Python | Go | TypeScript / JavaScript |
| --- | :---: | :---: | :---: |
| Module node | ✅ | ✅ | ✅ |
| Class | ✅ | — | ✅ (`enum` too, tagged) |
| Struct | — | ✅ | — |
| Interface | — | ✅ (with method members) | ✅ (methods + properties) |
| Function | ✅ | ✅ | ✅ (incl. arrow/function-expression consts) |
| Method | ✅ | ✅ (receiver in metadata) | ✅ (incl. arrow-valued fields) |
| Attribute / field | ✅ class attributes | ❌ struct fields not emitted | ✅ field definitions |
| Module-level variable / constant | ✅ | ❌ `const`/`var` blocks not extracted | ✅ |
| Type alias | ✅ (PEP 695) | ✅ | ✅ |
| Imports | ✅ `import`, `from … import` | ✅ (path normalised) | ✅ incl. re-export barrels and CommonJS `require` |
| Calls | ✅ | ✅ | ✅ (also inside nested functions) |
| Doc comment | ✅ docstring | ❌ | ✅ JSDoc |
| Export visibility | name heuristic (`_` prefix) | name heuristic (leading uppercase) | ✅ syntactic (`export` keyword, `module.exports`) |
| `async` flag | ✅ | n/a | ✅ |
| Decorators | ✅ | n/a | ✅ |
| Other metadata | — | receiver name and type | `abstract`, `static`, accessibility, private `#field` filtering |

Deliberately out of scope for TypeScript: namespaces (`internal_module`) and
module-level side-effect calls.

### Symbol roles

Beyond the node kind, Cograph infers a **role** for each symbol — entry point,
handler, model, test, and so on — from decorators and naming conventions. Web
framework decorators are recognised for FastAPI, Flask and NestJS
(`@Controller`, `@Resolver`, `@Injectable`), with name-suffix heuristics as the
fallback.

## Language-specific machinery

### Go build constraints

Go is the one language where the same package can legitimately have several
mutually exclusive implementations behind `//go:build` guards. Indexing all of
them would produce duplicate symbols with conflicting definitions, so Cograph
resolves a single build profile — derived from `go.mod` — and evaluates
constraints against it with three-valued logic.

Supported dimensions: `GOOS` (`linux`, `darwin`, `windows`), `GOARCH` (`amd64`,
`arm64`), and cgo on/off. Constraints outside what the evaluator understands, or
a genuine conflict between variants, surface as the dedicated error codes
`go_build_constraint_unsupported` and `go_build_variant_conflict` rather than
being silently mis-indexed.

Go also gets special handling for `func init()` and `func _()`, which are not
unique within a package: their qualified names are pinned to the file stem so
they do not collide.

### TypeScript and JavaScript noise filtering

JS/TS repositories carry large amounts of generated and vendored code, so the
discovery pass excludes it:

- directories: `node_modules`, `dist`, `build`, `.next`, `.nuxt`, `.output`,
  `.turbo`, `coverage`
- minified bundles: `*.min.js`, `*.min.mjs`, `*.min.cjs`
- any file over 1 MiB

Python and Go have neither a noise filter nor a size cap — their repositories do
not usually need one.

Incremental indexing handles the awkward cases these filters create. A rename out
of an indexable extension (`a.ts` → `a.txt`) is downgraded to a delete so stale
symbols do not linger, and churn confined to a pruned directory short-circuits
without touching the database at all.

## What happens to everything else

Three separate outcomes, depending on the file.

### It becomes a repository document

Prose and a specific set of non-source files are ingested by the
`index_repo_docs` step, chunked, embedded and searchable — this is the one way a
non-graph language reaches retrieval. The rules, in full:

| Included | Condition |
| --- | --- |
| `.md`, `.mdx`, `.rst` | anywhere in the tree |
| `.yml`, `.yaml` | under `.github/workflows/` |
| `.go`, `.py`, `.ts`, `.tsx`, `.js`, `.json`, `.toml`, `.yaml`, `.yml`, `.md`, `.mdx`, `.rst` | under `example`, `examples`, `sample`, `samples` |
| test files | under `test` / `tests`, named `test_*` or `*_test.go`, or with a doc extension |
| root config | `Dockerfile`, `Makefile`, `docker-compose.y*ml`, `compose.y*ml`, `go.mod`, `go.work`, `package.json`, `package-lock.json`, `pyproject.toml` |

So a root `Dockerfile` **is** retrievable prose, and a `.rs` file under
`examples/` is **not** — `.rs` is not in the example-extension set.

Every matched file is chunked and embedded the same way, whatever its kind. See
[Document RAG](/collections#repository-documents) for the chunker and the
exclusion list.

### It counts toward language statistics

A separate whole-checkout scan tallies bytes per language for the composition
chart on the repository Overview. This scan is deliberately much wider than the
four parsers, which is why the chart can show languages you cannot search.

::: info Seventeen icons, four parsers
The UI ships language icons for seventeen ecosystems, used to label those
statistics. That is a display concern, not a claim of seventeen-language
support.
:::

### It is ignored

Anything else is skipped at discovery and leaves no trace in the index. There is
no partial mode, no "text fallback", and no plan to grep the checkout at query
time.

## Adding a language

Graph support for a new language means, at minimum: a `GraphLanguage` entry with
its extensions and grammar, a walker that emits the node and edge kinds above,
call-target canonicalisation rules in the graph builder, and a fixture repository
plus extraction tests at parity with the existing four. The TypeScript walker and
its test suite are the reference for what "parity" means in practice.
