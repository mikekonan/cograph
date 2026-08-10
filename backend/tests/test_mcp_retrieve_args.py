"""Arg-level guards for `cograph_retrieve`: top_k clamp + mixed layer set.

These lock the two token-bounding decisions behind the tool: `top_k` is
clamped (not rejected) to a hard ceiling, and `mode="mixed"` no longer
surfaces the bare AST layer (which duplicated each CODE row's node).
"""
from __future__ import annotations

from backend.app.mcp.tools.retrieve import MAX_TOP_K, RetrieveToolArgs
from backend.app.rag.context_builder import RetrievalLayer


def _args(**kw) -> RetrieveToolArgs:
    base = {"query": "how does X work", "repository": "host/owner/name"}
    base.update(kw)
    return RetrieveToolArgs(**base)


def test_top_k_above_max_is_clamped_not_rejected():
    assert _args(top_k=40).top_k == MAX_TOP_K
    assert _args(top_k=1000).top_k == MAX_TOP_K


def test_top_k_within_bound_is_unchanged():
    assert _args(top_k=5).top_k == 5
    assert _args().top_k == 10  # default


def test_mixed_mode_excludes_bare_ast():
    layers = _args(mode="mixed").resolved_layers()
    assert RetrievalLayer.AST not in layers
    assert layers == {
        RetrievalLayer.CODE,
        RetrievalLayer.AST_SUMMARY,
        RetrievalLayer.REPO_DOC,
    }


def test_code_mode_keeps_ast_for_power_users():
    assert RetrievalLayer.AST in _args(mode="code").resolved_layers()


def test_explicit_stores_override_mode():
    # Power users can still ask for the bare AST layer explicitly.
    layers = _args(mode="mixed", stores=[RetrievalLayer.AST]).resolved_layers()
    assert layers == {RetrievalLayer.AST}
