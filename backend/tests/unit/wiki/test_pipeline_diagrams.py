"""Tests for the Markdown and Mermaid clean-up passes run on page drafts."""

from __future__ import annotations

from backend.app.wiki.manifests import (
    Dependency,
    ManifestEvidence,
    PublicApiEntry,
    RepoManifests,
    RunCommand,
)
from backend.app.wiki.citations import RepositorySlug
from backend.app.wiki.pipeline import (
    _select_manifest_lines,
    sanitize_mermaid_in_markdown,
    sanitize_page_links_in_markdown,
    upgrade_multiline_inline_code,
)
from backend.app.wiki.schemas import (
    PageSpec,
    ReaderQuestion,
)

def test_select_manifest_lines_picks_relevant_axes() -> None:
    manifests = RepoManifests(
        public_api=[
            PublicApiEntry(
                kind="function",
                qualified_name="src.cli.main",
                file_path="src/cli.py",
                start_line=1,
            )
        ],
        dependencies=[
            Dependency(
                ecosystem="python",
                name="fastapi",
                version="0.110.0",
                evidence=ManifestEvidence(
                    source_file_path="pyproject.toml",
                    source_lines=(12, 12),
                ),
            )
        ],
        run_commands=[
            RunCommand(
                kind="docker",
                label="docker compose up",
                evidence=ManifestEvidence(
                    source_file_path="docker-compose.yml",
                    source_lines=(1, 1),
                ),
            )
        ],
    )
    spec = PageSpec(
        slug="index",
        title="Overview",
        purpose="Landing",
        diagram=True,
        covers_questions=[ReaderQuestion.PUBLIC_API, ReaderQuestion.HOW_TO_RUN],
    )
    lines = _select_manifest_lines(manifests=manifests, spec=spec)
    assert any("[api] src.cli.main" in line for line in lines)
    assert any("[dep:python] fastapi 0.110.0" in line for line in lines)
    assert any("[run:docker] docker compose up" in line for line in lines)


# ---------------------------------------------------------------------------
# sanitize_mermaid_in_markdown — defensive label-quoting pass
# ---------------------------------------------------------------------------


def test_sanitize_mermaid_quotes_label_with_parens():
    """Real failure mode: `[Spec(w,r)]` blew up Mermaid with `got 'PS'`.
    Sanitizer must wrap the label in double quotes so the parser accepts it."""
    md = "## Architecture\n\n```mermaid\nflowchart LR\n  spec --> serve[Spec(w,r)]\n```\n"
    out = sanitize_mermaid_in_markdown(md)
    assert 'serve["Spec(w,r)"]' in out


def test_sanitize_mermaid_quotes_and_wraps_label_with_slash():
    """Long `/`-separated label is quoted (the `/` is a Mermaid breaker)
    AND wrapped for box-fit (39 chars > threshold)."""
    md = "```mermaid\nflowchart LR\n  W[wrapper / wrapperBody / wrapperSecurity]\n```"
    out = sanitize_mermaid_in_markdown(md)
    # Quoted (so `/` doesn't break the parser) and wrapped on `/` boundaries.
    assert 'W["' in out
    assert "<br/>" in out
    assert "wrapper" in out and "wrapperBody" in out and "wrapperSecurity" in out


def test_sanitize_mermaid_leaves_short_safe_labels_alone():
    md = "```mermaid\nflowchart LR\n  G[short.Name] --> C[ok]\n```"
    out = sanitize_mermaid_in_markdown(md)
    assert out == md


def test_sanitize_mermaid_wraps_long_dotted_label():
    """A long `pkg.Type.method`-style label is split on `.` boundaries
    and joined with `<br/>` so it fits the node box at narrow viewports."""
    md = "```mermaid\nflowchart LR\n  G[generator.Generator.Generate] --> C[components]\n```"
    out = sanitize_mermaid_in_markdown(md)
    # Wrapped + quoted (mermaid requires quotes around HTML labels).
    assert 'G["generator.<br/>Generator.Generate"]' in out
    # Short sibling (`components`, 10 chars) is below threshold — left alone.
    assert "C[components]" in out


def test_sanitize_mermaid_wraps_camel_case_label():
    """A long camelCase label with no separator splits on word
    boundaries — `processSubscriptionRenewal` → 3 lines."""
    md = (
        "```mermaid\nflowchart LR\n"
        "  S --> p[processSubscriptionRenewal]\n```"
    )
    out = sanitize_mermaid_in_markdown(md)
    assert 'p["process<br/>Subscription<br/>Renewal"]' in out


def test_sanitize_mermaid_truncates_at_three_lines_and_injects_title():
    """Labels that would wrap into more than 3 visual lines get capped at
    3 with `…` on the third line, and the full original text survives in
    a `title=''` HTML attribute on a wrapping `<span>` so the reader can
    hover to recover the elided suffix."""
    # 7 dotted segments → 7 tokens. With an 18-char target line and the
    # `pkg.A.subPkg.CustomerBillingAddressNormaliser.helper.bar.baz` shape,
    # the wrapper produces more than 3 lines and the cap kicks in.
    long = "domain.shipping.customers.CustomerBillingAddressNormaliser.helper.normalise.foo"
    md = f"```mermaid\nflowchart LR\n  N[{long}] --> X[ok]\n```"
    out = sanitize_mermaid_in_markdown(md)
    # The wrapping `<span title='...'>` carries the FULL label.
    assert f"title='{long}'" in out
    # The visible text is at most 3 `<br/>`-separated lines and ends with `…`.
    assert "…</span>" in out
    visible = out.split("title=", 1)[1].split(">", 1)[1].split("</span>", 1)[0]
    assert visible.count("<br/>") <= 2  # 3 lines = at most 2 separators
    assert visible.endswith("…")


def test_sanitize_mermaid_truncation_is_idempotent():
    """A second sanitize pass on a truncated label must be a no-op —
    the existing `<br/>`-bearing text guards the wrap function so we
    don't gain a nested `<span>` or extra `<br/>` tokens."""
    long = "domain.shipping.customers.CustomerBillingAddressNormaliser.helper.normalise.foo"
    md = f"```mermaid\nflowchart LR\n  N[{long}] --> X[ok]\n```"
    once = sanitize_mermaid_in_markdown(md)
    twice = sanitize_mermaid_in_markdown(once)
    assert once == twice


def test_sanitize_mermaid_truncation_escapes_html_in_title():
    """The `title=''` payload uses single-quote syntax, so any apostrophe
    or HTML-meaningful character in the original label must be entity-
    escaped to avoid premature attribute closure / accidental injection."""
    # Label long enough to truncate, with apostrophe + angle-brackets.
    long = "domain.<script>.alpha.beta.gamma.delta.epsilon.zeta.eta.theta"
    md = f"```mermaid\nflowchart LR\n  N[{long}] --> X[ok]\n```"
    out = sanitize_mermaid_in_markdown(md)
    # Literal `<script>` must NOT survive inside the title attribute.
    # We slice the title attribute payload only — the diagram body itself
    # legitimately contains `<br/>` and `</span>` tags.
    title_payload = out.split("title='", 1)[1].split("'>", 1)[0]
    assert "<script>" not in title_payload
    assert "&lt;script&gt;" in title_payload


def test_sanitize_mermaid_wrap_is_idempotent():
    """Running the sanitiser twice must produce the same output as
    once — pages that have already been wrapped won't gain extra
    `<br/>` tokens on re-render."""
    md = "```mermaid\nflowchart LR\n  G[generator.Generator.Generate] --> C[ok]\n```"
    once = sanitize_mermaid_in_markdown(md)
    twice = sanitize_mermaid_in_markdown(once)
    assert once == twice


def test_sanitize_mermaid_no_wrap_in_classdiagram():
    """The wrap pass is flowchart-only — class/state/sequence diagrams
    use different label containers and `<br/>` would break them."""
    md = (
        "```mermaid\nclassDiagram\n"
        "  class verylong.qualified.classname.Method {\n  }\n```"
    )
    out = sanitize_mermaid_in_markdown(md)
    # No `<br/>` injection in classDiagram fences.
    assert "<br/>" not in out


def test_sanitize_mermaid_idempotent_on_already_quoted():
    md = '```mermaid\nflowchart LR\n  n["already (quoted)"]\n```'
    out = sanitize_mermaid_in_markdown(md)
    assert out == md


def test_sanitize_mermaid_only_touches_mermaid_fences():
    md = '```python\nx = "foo (bar)"\n```\n\nplain text [Spec(w,r)] outside fences'
    out = sanitize_mermaid_in_markdown(md)
    assert out == md


def test_sanitize_mermaid_handles_curly_label():
    md = "```mermaid\nflowchart TD\n  A{decision: yes/no} --> B[end]\n```"
    out = sanitize_mermaid_in_markdown(md)
    assert 'A{"decision: yes/no"}' in out


def test_sanitize_mermaid_quotes_rest_route_with_path_var():
    """Real failure: `[GET /dev/users/{username}/totp/generate]` blew up
    Mermaid with `got 'DIAMOND_START'` because the unquoted `{` is parsed
    as the start of a diamond shape. Sanitizer must wrap the whole label
    in quotes (and now also break it onto multiple visual lines)."""
    md = (
        "```mermaid\nflowchart LR\n"
        "  client --> route[GET /dev/users/{username}/totp/generate]\n"
        "```"
    )
    out = sanitize_mermaid_in_markdown(md)
    assert 'route["' in out
    # `{username}` survives in quoted form (the parser never sees the bare
    # `{` because the whole label is quoted).
    assert "{username}" in out


def test_sanitize_mermaid_quotes_label_with_multiple_path_vars():
    md = (
        "```mermaid\nflowchart LR\n"
        "  a --> b[POST /repos/{repo_id}/jobs/{job_id}/cancel]\n"
        "```"
    )
    out = sanitize_mermaid_in_markdown(md)
    assert 'b["' in out
    assert "{repo_id}" in out
    assert "{job_id}" in out


def test_sanitize_mermaid_escapes_semicolon_in_sequence_message():
    """Real failure: `D->>W: NewValidationServer(...); Start()` —
    Mermaid treats `;` as a statement separator, so the message is
    chopped at the semicolon and the parser then sees `Start()` and
    expects an arrow. Replace with `&#59;` so the rendered text
    still shows a semicolon but the parser sees a single message."""
    md = (
        "```mermaid\nsequenceDiagram\n"
        "  participant D\n  participant W\n"
        "  D->>W: NewValidationServer(...); Start()\n"
        "```"
    )
    out = sanitize_mermaid_in_markdown(md)
    assert "D->>W: NewValidationServer(...)&#59; Start()" in out
    # No raw semicolons survive in the message body — only the entity form.
    message_line = out.split("D->>W:")[1].split("\n")[0]
    assert ";" not in message_line.replace("&#59;", "")


def test_sanitize_mermaid_keeps_semicolons_outside_sequence_diagrams():
    """Flowchart labels can contain `;` because they get caught by the
    quoting pass (`;` is in `_MERMAID_LABEL_BREAKERS`). The semicolon
    escape is scoped to sequenceDiagram bodies only."""
    md = (
        "```mermaid\nflowchart LR\n"
        "  a --> b[do_thing(x; y)]\n"
        "```"
    )
    out = sanitize_mermaid_in_markdown(md)
    assert 'b["do_thing(x; y)"]' in out
    assert "&#59;" not in out


def test_sanitize_mermaid_rewrites_double_quoted_class_names():
    """Real failure: `class \"example.Foo\" {` — Mermaid's classDiagram
    rejects double-quoted class names. Backtick-quoted names parse
    cleanly. Both the declaration and any relationship-line references
    must be rewritten so they still match."""
    md = (
        "```mermaid\nclassDiagram\n"
        '  class "example.Foo" {\n'
        "    +int Amount\n"
        "  }\n"
        '  class "example.Bar" {\n'
        "    +string Name\n"
        "  }\n"
        '  "example.Foo" --> "example.Bar" : owns\n'
        "```"
    )
    out = sanitize_mermaid_in_markdown(md)
    assert "class `example.Foo`" in out
    assert "class `example.Bar`" in out
    assert "`example.Foo` --> `example.Bar`" in out
    assert '"example.' not in out


# ---------------------------------------------------------------------------
# sanitize_page_links_in_markdown — defensive broken-link stripper
# ---------------------------------------------------------------------------


_REPO_SLUG_FOR_LINKS = RepositorySlug(
    host="github.com", owner="acme", name="widgets"
)
_REPO_PATH_FOR_LINKS = "/repos/github.com/acme/widgets"


def test_sanitize_page_links_strips_unknown_sibling_slug():
    md = "See [Generator](./generator) for the writer."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs={"index", "architecture"},
        doc_slug_by_path={},
    )
    assert out == "See Generator for the writer."


def test_sanitize_page_links_keeps_known_sibling_slug():
    md = "See [Architecture](./architecture)."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs={"architecture", "index"},
        doc_slug_by_path={},
    )
    assert out == md


def test_sanitize_page_links_rewrites_raw_doc_url_to_slug():
    md = f"See [Spec]({_REPO_PATH_FOR_LINKS}/docs/example/spec.md)."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={"example/spec.md": "example-spec"},
    )
    assert f"[Spec]({_REPO_PATH_FOR_LINKS}/docs/example-spec)" in out


def test_sanitize_page_links_strips_raw_non_markdown_doc_url():
    md = f"See [Mod file]({_REPO_PATH_FOR_LINKS}/docs/foo/go.mod)."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={},
    )
    assert out == "See Mod file."


def test_sanitize_page_links_strips_raw_graph_path():
    md = f"See [Type]({_REPO_PATH_FOR_LINKS}/graph/node/abc)."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={},
    )
    assert out == "See Type."


def test_sanitize_page_links_leaves_graph_query_url_alone():
    """The canonical `?node=` form is what `_node_anchor` emits — keep it."""
    md = f"See [Type]({_REPO_PATH_FOR_LINKS}/graph?node=abc)."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={},
    )
    assert out == md


def test_sanitize_page_links_drops_list_bullet_when_sibling_unknown():
    """Real failure mode: agent invents `- [Getting Started](./getting-started)`
    in a Related Pages list. Sanitizer must drop the whole bullet, not
    leave a misleading bare `- Getting Started` behind."""
    md = (
        "## Related Pages\n\n"
        "- [Getting Started](./getting-started)\n"
        "- [Architecture](./architecture)\n"
        "- [Async and Effects](./async-effects)\n"
    )
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs={"index", "architecture"},
        doc_slug_by_path={},
    )
    assert "Getting Started" not in out
    assert "Async and Effects" not in out
    assert "[Architecture](./architecture)" in out


def test_sanitize_page_links_keeps_inline_label_when_sibling_unknown():
    """Inline mention (not a list bullet) — strip URL, keep label as
    prose so the surrounding sentence still parses."""
    md = "See [Getting Started](./getting-started) before you continue."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs={"index", "architecture"},
        doc_slug_by_path={},
    )
    assert out == "See Getting Started before you continue."


def test_sanitize_page_links_flattens_line_range_url():
    """`Source: [path](L10-L24)` is broken markdown — the URL is a bare
    line range and the FE resolves it relative to the current page →
    `/repos/<host>/<owner>/<name>/wiki/L10-L24` (404). Flatten to plain
    text."""
    md = "Source: [generator/generator.go](L81-L125)"
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={},
    )
    assert out == "Source: generator/generator.go:L81-L125"


def test_sanitize_page_links_flattens_bare_line_token():
    md = "see [foo.py](L42)"
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={},
    )
    assert out == "see foo.py:L42"


def test_sanitize_page_links_preserves_fragment_on_rewrite():
    md = f"See [Stages]({_REPO_PATH_FOR_LINKS}/docs/docs/architecture.md#stages)."
    out = sanitize_page_links_in_markdown(
        markdown=md,
        repo_slug=_REPO_SLUG_FOR_LINKS,
        known_page_slugs=set(),
        doc_slug_by_path={"docs/architecture.md": "architecture"},
    )
    assert (
        f"[Stages]({_REPO_PATH_FOR_LINKS}/docs/architecture#stages)" in out
    )


# ---------------------------------------------------------------------------
# upgrade_multiline_inline_code — defensive code-fence repair
# ---------------------------------------------------------------------------


def test_upgrade_multiline_inline_code_promotes_function_body() -> None:
    """Real failure mode: writer wrapped a Go function in single backticks.
    Sanitizer must convert to a fenced block so the renderer treats it as
    a code block, not a dark inline pill with delimiters leaking through."""
    md = (
        "Bootstrap entry point.\n\n"
        "`func (self *application) Initialize(ctx context.Context) (err error) {\n"
        "  if self.state.Context != nil { return errors.New(\"already initialized\") }\n"
        "  return\n"
        "}`\n"
    )
    out = upgrade_multiline_inline_code(md)
    assert "```\nfunc (self *application) Initialize" in out
    assert out.endswith("}\n```\n")


def test_upgrade_multiline_inline_code_leaves_short_inline_alone() -> None:
    """Short inline code without a newline is left as-is — that's a legit
    identifier reference, not a function body regression."""
    md = "Use `pkg.Function` for the public API."
    out = upgrade_multiline_inline_code(md)
    assert out == md


def test_upgrade_multiline_inline_code_leaves_inline_without_code_chars() -> None:
    """Multi-line inline code without `{}();` is probably a quoted prose
    snippet, not source. Don't promote — that would over-fire."""
    md = "He said `hello\nworld` in two lines."
    out = upgrade_multiline_inline_code(md)
    assert out == md


def test_upgrade_multiline_inline_code_does_not_pair_spans_across_lines() -> None:
    """Real failure mode: the closing backtick of `makeComponent` paired with
    the opening backtick on the next table row, and the text between them
    became a fenced block that broke the table."""
    md = (
        "| `Close() error` | Required by `makeComponent`; supplies the completion channel or an error to callers. |\n"
        "| [`app.gen.ShelfHandler`](/x) | routes (shelves) |\n"
    )
    assert upgrade_multiline_inline_code(md) == md


def test_upgrade_multiline_inline_code_leaves_existing_fences_alone() -> None:
    """Triple-backtick fences already render correctly — the regex's bare-
    backtick anchors must not match inside or across them."""
    md = "```go\nfunc Main() { return }\n```\n"
    out = upgrade_multiline_inline_code(md)
    assert out == md
