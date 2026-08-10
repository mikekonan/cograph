from __future__ import annotations

import pytest

from backend.app.md_rag.parser import MarkdownParser


@pytest.fixture
def parser() -> MarkdownParser:
    return MarkdownParser()


def test_parse_frontmatter(parser: MarkdownParser) -> None:
    text = """---
title: Hello World
tags: intro
---
# Main Title
Some body text.
"""
    result = parser.parse(text)
    assert result.title == "Hello World"
    assert result.frontmatter.get("title") == "Hello World"
    assert result.frontmatter.get("tags") == "intro"


def test_parse_headings(parser: MarkdownParser) -> None:
    text = """# H1
## H2 A
### H3
## H2 B
"""
    result = parser.parse(text)
    assert len(result.heading_tree) == 4
    assert result.heading_tree[0]["level"] == 1
    assert result.heading_tree[0]["text"] == "H1"
    assert result.heading_tree[1]["level"] == 2


def test_parse_code_blocks(parser: MarkdownParser) -> None:
    text = """```python
def foo():
    pass
```
Some text.
```bash
echo hello
```
"""
    result = parser.parse(text)
    assert len(result.code_blocks) == 2
    assert result.code_blocks[0]["language"] == "python"
    assert "def foo()" in result.code_blocks[0]["content"]
    assert result.code_blocks[1]["language"] == "bash"


def test_parse_links(parser: MarkdownParser) -> None:
    text = """[link](path/to/file.md) and [[Wiki Link]]
"""
    result = parser.parse(text)
    links = {link["href"] for link in result.links}
    assert "path/to/file.md" in links
    assert "Wiki Link" in links


def test_parse_tables(parser: MarkdownParser) -> None:
    text = """| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |
"""
    result = parser.parse(text)
    assert len(result.tables) == 1
    table = result.tables[0]
    assert table["header"] == ["A", "B"]
    assert table["rows"] == [["1", "2"], ["3", "4"]]


def test_word_and_line_count(parser: MarkdownParser) -> None:
    text = "line one\nline two\nline three"
    result = parser.parse(text)
    assert result.line_count == 3
    assert result.word_count == 6
