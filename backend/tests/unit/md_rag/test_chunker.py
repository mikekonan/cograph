from __future__ import annotations

import pytest

from backend.app.md_rag.chunker import MdChunker


@pytest.fixture
def chunker() -> MdChunker:
    return MdChunker(max_chars=500, min_chars=100)


def test_chunk_respects_headings(chunker: MdChunker) -> None:
    text = """# Section 1
Line one.
Line two.
# Section 2
Line three.
Line four.
"""
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    assert "Section 1" in chunks[0].heading_path


def test_chunk_heading_path(chunker: MdChunker) -> None:
    text = """# A
## B
Content under B.
## C
Content under C.
"""
    chunks = chunker.chunk(text)
    assert len(chunks) >= 1
    b_chunks = [c for c in chunks if "B" in c.heading_path]
    if b_chunks:
        assert b_chunks[0].heading_path == ["A", "B"]


def test_chunk_oversized_section(chunker: MdChunker) -> None:
    paragraph = "word " * 200
    text = f"# Big Section\n{paragraph}\n\n{paragraph}\n"
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert "Big Section" in chunk.heading_path


def test_chunker_default_max_chars_is_4000() -> None:
    c = MdChunker()
    assert c.max_chars == 4000
    assert c.min_chars == 400
    assert c.max_chunks == 512


def test_chunker_merged_sections_use_common_heading_path() -> None:
    text = """# Root
## Child A
Tiny A content.
## Child B
Tiny B content.
"""
    c = MdChunker(max_chars=2000, min_chars=10)
    chunks = c.chunk(text)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["Root"]
    assert chunks[0].heading_level == 1


def test_chunker_divergent_merge_falls_back_to_first() -> None:
    text = """# Alpha
Tiny content under Alpha.
# Beta
Tiny content under Beta.
"""
    c = MdChunker(max_chars=2000, min_chars=10)
    chunks = c.chunk(text)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ["Alpha"]


def test_chunker_tail_below_min_merges_into_previous() -> None:
    big = "lorem ipsum dolor sit amet " * 30
    text = f"""# First
{big}

# Second
short tail
"""
    c = MdChunker(max_chars=800, min_chars=200)
    chunks = c.chunk(text)
    assert len(chunks) == 1
    assert "short tail" in chunks[0].content
    assert "First" in chunks[0].heading_path or "Alpha" in chunks[0].heading_path or chunks[0].heading_path[0] == "First"


def test_chunker_solo_small_doc_emits_unchanged() -> None:
    text = "# Tiny\nJust a sentence.\n"
    c = MdChunker(max_chars=2000, min_chars=200)
    chunks = c.chunk(text)
    assert len(chunks) == 1
    assert "Just a sentence." in chunks[0].content


def test_chunker_never_splits_mid_table() -> None:
    rows = "\n".join(f"| col1 row {i} | col2 row {i} | col3 row {i} |" for i in range(60))
    text = f"""# Big Table

| col1 | col2 | col3 |
|------|------|------|
{rows}
"""
    c = MdChunker(max_chars=500, min_chars=100)
    chunks = c.chunk(text)
    table_chunks = [c_ for c_ in chunks if "| col1" in c_.content or "| col2 row" in c_.content]
    assert table_chunks, "expected at least one chunk containing table rows"
    for ch in table_chunks:
        lines = ch.content.splitlines()
        pipe_lines = [ln for ln in lines if ln.lstrip().startswith("|")]
        for ln in pipe_lines:
            assert ln.lstrip().startswith("|") and ln.rstrip().endswith("|"), (
                f"table row truncated: {ln!r} in chunk {ch.chunk_index}"
            )


def test_chunker_never_splits_mid_code_fence() -> None:
    code_body = "\n".join(f"line_{i} = compute({i})" for i in range(80))
    text = f"""# Code

```python
{code_body}
```
"""
    c = MdChunker(max_chars=400, min_chars=100)
    chunks = c.chunk(text)
    code_chunks = [c_ for c_ in chunks if "```" in c_.content]
    for ch in code_chunks:
        opens = ch.content.count("```")
        assert opens % 2 == 0, (
            f"unbalanced code fence in chunk {ch.chunk_index}: {ch.content[:200]!r}"
        )


def test_chunker_emits_oversize_atomic_block_intact() -> None:
    huge_row = "| " + ("x " * 800) + "|"
    text = f"""# Wide

| header |
|--------|
{huge_row}
"""
    c = MdChunker(max_chars=200, min_chars=50)
    chunks = c.chunk(text)
    intact = [ch for ch in chunks if huge_row in ch.content]
    assert intact, "oversize atomic table row should be emitted intact, not truncated"


def test_chunker_table_rows_all_intact_with_small_budget() -> None:
    rows = [f"| {i} | row {i} |" for i in range(20)]
    text = "# T\n\n| A | B |\n|---|---|\n" + "\n".join(rows) + "\n"
    c = MdChunker(max_chars=120, min_chars=40)
    chunks = c.chunk(text)
    seen = "\n".join(ch.content for ch in chunks)
    for row in rows:
        assert row in seen, f"row missing from output: {row!r}"
