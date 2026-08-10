# `go_types` graph fixture

Derived from `github.com/mikekonan/go-types` at commit `45105ba8f957102cc7b92854fe385280baa9120f`.

This fixture is intentionally reduced for graph parser/extractor/ingest tests:

- It preserves the real module path: `github.com/mikekonan/go-types/v2`
- It keeps the original package layout used by the graph engine
- It is not meant to compile; undefined generated data/types are acceptable because tests only parse and ingest syntax trees

License: MIT. See `LICENSE` in this fixture directory.
