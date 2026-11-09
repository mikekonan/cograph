"""Arg-level guards for `cograph_retrieve`: top_k clamp + mixed layer set.

These lock the two token-bounding decisions behind the tool: `top_k` is
clamped (not rejected) to a hard ceiling, and a mode asks only for the
layers whose rows reach the output: the body and docs, not the signature
or summary rows of the same node.
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


def test_modes_ask_only_for_rows_the_output_shows():
    assert _args(mode="mixed").resolved_layers() == {
        RetrievalLayer.CODE,
        RetrievalLayer.REPO_DOC,
    }
    assert _args(mode="code").resolved_layers() == {RetrievalLayer.CODE}


def test_explicit_stores_override_mode():
    # Power users can still ask for the bare AST layer explicitly.
    layers = _args(mode="mixed", stores=[RetrievalLayer.AST]).resolved_layers()
    assert layers == {RetrievalLayer.AST}


def test_a_summary_store_returns_the_code_of_the_hit():
    def stores(*layers):
        return _args(stores=list(layers)).resolved_layers()

    assert stores(RetrievalLayer.AST_SUMMARY) == {RetrievalLayer.CODE}
    assert stores(RetrievalLayer.AST, RetrievalLayer.CODE) == {RetrievalLayer.CODE}
    assert stores(RetrievalLayer.AST_SUMMARY, RetrievalLayer.REPO_DOC) == {
        RetrievalLayer.CODE,
        RetrievalLayer.REPO_DOC,
    }
