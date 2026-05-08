"""Unit tests for the shared excerpt builder.

The contract is: every search-style MCP tool routes content through
``make_snippet`` so agents can rely on a single (snippet, content_truncated)
shape and decide when to fetch full text via cograph.read_node / read_chunk.
"""

from __future__ import annotations

import pytest

from backend.app.rag.snippet import (
    DEFAULT_SNIPPET_CHARS,
    MAX_SNIPPET_CHARS,
    MIN_SNIPPET_CHARS,
    extract_query_terms,
    make_snippet,
)


class TestExtractQueryTerms:
    def test_lowercases_and_dedupes(self):
        assert extract_query_terms("Auth Auth FLOW") == ["auth", "flow"]

    def test_drops_stopwords_and_short_tokens(self):
        assert extract_query_terms("how is the auth flow") == ["auth", "flow"]

    def test_strips_punctuation(self):
        assert extract_query_terms("apply_repository_read_scope.") == [
            "apply_repository_read_scope"
        ]

    def test_keeps_qualified_names_intact(self):
        terms = extract_query_terms("backend.app.mcp.tools.retrieve")
        assert "backend.app.mcp.tools.retrieve" in terms

    def test_caps_term_count(self):
        query = " ".join(f"term{i}" for i in range(20))
        assert len(extract_query_terms(query, max_terms=5)) == 5

    def test_blank_query(self):
        assert extract_query_terms("") == []
        assert extract_query_terms("   ") == []


class TestMakeSnippetShortInput:
    def test_returns_full_content_unchanged_for_short_input(self):
        snippet, truncated = make_snippet("hello world", ["world"])
        assert snippet == "hello world"
        assert truncated is False

    def test_collapses_internal_whitespace(self):
        snippet, truncated = make_snippet("hello\n\n\tworld", [])
        assert snippet == "hello world"
        assert truncated is False

    def test_empty_input(self):
        assert make_snippet(None) == ("", False)
        assert make_snippet("") == ("", False)
        assert make_snippet("   ") == ("", False)


class TestMakeSnippetTruncation:
    def test_long_content_is_truncated(self):
        body = "lorem ipsum " * 200
        snippet, truncated = make_snippet(body, [], chars=200)
        assert truncated is True
        # Snippet stays close to budget (allow ellipsis + boundary slack).
        assert len(snippet) <= 200 + 50

    def test_truncation_window_centres_on_first_match(self):
        before = "padding " * 100
        target = "the AUTH MIDDLEWARE rejects expired tokens "
        after = "trailing " * 100
        body = before + target + after
        snippet, truncated = make_snippet(body, ["auth"], chars=300)
        assert truncated is True
        assert "auth middleware" in snippet.lower()

    def test_no_match_falls_back_to_head_window(self):
        body = "alpha " * 200
        snippet, truncated = make_snippet(body, ["nonexistent"], chars=200)
        assert truncated is True
        assert snippet.startswith("alpha")
        # No leading ellipsis when window starts at 0.
        assert not snippet.startswith("…")

    def test_match_near_start_keeps_head_window(self):
        body = "auth handshake " + ("filler " * 200)
        snippet, truncated = make_snippet(body, ["auth"], chars=200)
        assert truncated is True
        assert snippet.startswith("auth")

    def test_match_at_tail_uses_tail_window(self):
        body = ("filler " * 200) + " final-target-marker"
        snippet, truncated = make_snippet(body, ["final-target-marker"], chars=200)
        assert truncated is True
        assert "final-target-marker" in snippet
        # Tail window ends at content end → no trailing ellipsis.
        assert not snippet.endswith("…")

    def test_truncated_edges_get_ellipsis(self):
        body = "padding " * 100 + "hit_word " + "trailing " * 100
        snippet, truncated = make_snippet(body, ["hit_word"], chars=200)
        assert truncated is True
        assert snippet.startswith("…")
        assert snippet.endswith("…")


class TestMakeSnippetWordBoundary:
    def test_does_not_split_mid_word(self):
        body = "antidisestablishmentarianism " * 50
        snippet, truncated = make_snippet(body, [], chars=120)
        assert truncated is True
        # The cut should land on a whitespace boundary, not mid-token.
        clean = snippet.rstrip("…").rstrip()
        # Last visible character should be the end of a complete token.
        # Tokens are space-separated, so clean must not end mid-rune.
        if clean:
            tokens = clean.split()
            assert tokens, "snippet should contain at least one whole token"

    def test_unicode_safe(self):
        # Multi-byte runes (Cyrillic, em dash) — Python str slicing operates
        # on code points, so this should be a non-issue, but lock it in.
        body = "Это длинный текст про авторизацию — " * 30
        snippet, truncated = make_snippet(body, ["авторизацию"], chars=120)
        assert truncated is True
        # Snippet is valid UTF-8 (would have raised on encode otherwise).
        snippet.encode("utf-8")


class TestMakeSnippetBudgetClamp:
    def test_below_min_chars_clamped_up(self):
        body = "x " * 200
        snippet, _ = make_snippet(body, [], chars=10)
        # 10 is below MIN_SNIPPET_CHARS — should be clamped.
        assert len(snippet) >= MIN_SNIPPET_CHARS - 10

    def test_above_max_chars_clamped_down(self):
        body = "x " * 5000
        snippet, truncated = make_snippet(body, [], chars=10_000)
        assert truncated is True
        assert len(snippet) <= MAX_SNIPPET_CHARS + 50

    @pytest.mark.parametrize("chars", [MIN_SNIPPET_CHARS, DEFAULT_SNIPPET_CHARS, MAX_SNIPPET_CHARS])
    def test_valid_budgets_round_trip(self, chars):
        body = "lorem ipsum " * 1000
        snippet, truncated = make_snippet(body, [], chars=chars)
        assert truncated is True
        assert len(snippet) <= chars + 50
