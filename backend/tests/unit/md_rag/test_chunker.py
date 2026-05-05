from __future__ import annotations

import pytest

from backend.app.md_rag.chunker import MdChunker


@pytest.fixture
def chunker() -> MdChunker:
    return MdChunker(max_chars=500)


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
    # First chunk should start with Section 1 heading
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
    # Chunks under B should have path ["A", "B"]
    b_chunks = [c for c in chunks if "B" in c.heading_path]
    if b_chunks:
        assert b_chunks[0].heading_path == ["A", "B"]


def test_chunk_oversized_section(chunker: MdChunker) -> None:
    # Create a section larger than max_chars
    paragraph = "word " * 200
    text = f"# Big Section\n{paragraph}\n{paragraph}\n"
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    # All chunks from this section share the same heading path
    for chunk in chunks:
        assert "Big Section" in chunk.heading_path
