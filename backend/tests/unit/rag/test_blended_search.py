from backend.app.rag.blended_search import (
    BlendedSearchGroup,
    BlendedSearchGroupKind,
    BlendedSearchResult,
    rank_blended_groups,
)


def test_rank_blended_groups_puts_wiki_first_for_broad_queries() -> None:
    groups = [
        _group(BlendedSearchGroupKind.CODE, score=30.0),
        _group(BlendedSearchGroupKind.WIKI, score=10.0),
    ]

    ranked = rank_blended_groups(groups, query="How does Option map to Result?")

    assert [group.kind for group in ranked] == [
        BlendedSearchGroupKind.WIKI,
        BlendedSearchGroupKind.CODE,
    ]
    assert [group.rank for group in ranked] == [1, 2]


def test_rank_blended_groups_keeps_exact_symbols_first() -> None:
    groups = [
        _group(BlendedSearchGroupKind.WIKI, score=40.0),
        _group(BlendedSearchGroupKind.CODE, score=1.0, exact_symbol_match=True),
    ]

    ranked = rank_blended_groups(groups, query="svc.raise_repo_not_ready")

    assert [group.kind for group in ranked] == [
        BlendedSearchGroupKind.CODE,
        BlendedSearchGroupKind.WIKI,
    ]


def _group(
    kind: BlendedSearchGroupKind,
    *,
    score: float,
    exact_symbol_match: bool = False,
) -> BlendedSearchGroup:
    return BlendedSearchGroup(
        kind=kind,
        title=kind.value,
        rank=0,
        score=score,
        results=[
            BlendedSearchResult(
                id=f"{kind.value}:1",
                title=kind.value,
                snippet=kind.value,
                score=score,
                metadata={"exact_symbol_match": exact_symbol_match},
            )
        ],
    )
