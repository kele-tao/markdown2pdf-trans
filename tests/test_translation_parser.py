"""Markdown 解析层测试：front matter、setext 标题、表格、列表、代码与公式的完整性。"""
from __future__ import annotations

from src.translation.markdown_parser import BlockKind, parse_markdown


def _kinds(md: str) -> list[BlockKind]:
    return [block.kind for block in parse_markdown(md).blocks]


def test_front_matter_requires_yaml_key():
    with_front_matter = "---\ntitle: Guide\nauthor: Team\n---\n\nBody.\n"
    blocks = parse_markdown(with_front_matter).blocks
    assert blocks[0].kind is BlockKind.FRONT_MATTER
    assert "title: Guide" in blocks[0].text
    assert blocks[0].number == 0

    horizontal_rules = "---\n\nBody.\n\n---\n"
    assert _kinds(horizontal_rules) == [
        BlockKind.HR,
        BlockKind.PARAGRAPH,
        BlockKind.HR,
    ]


def test_setext_headings_and_heading_paths():
    doc = "Title\n=====\n\nSection\n-------\n\nBody.\n"
    blocks = parse_markdown(doc).blocks

    assert [block.kind for block in blocks] == [
        BlockKind.HEADING,
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
    ]
    assert [block.level for block in blocks[:2]] == [1, 2]
    assert blocks[0].path == ("Title",)
    assert blocks[1].path == ("Title", "Section")
    assert blocks[2].path == ("Title", "Section")


def test_nested_list_with_blank_line_stays_one_block():
    doc = "- top\n  - child\n\n- second\n\nAfter.\n"
    blocks = parse_markdown(doc).blocks

    assert [block.kind for block in blocks] == [BlockKind.LIST, BlockKind.PARAGRAPH]
    assert "- child" in blocks[0].text
    assert "- second" in blocks[0].text


def test_table_without_outer_pipes_stays_one_block():
    doc = "Name | Value\n--- | ---\nA | 1\nB | 2\n\nText.\n"
    blocks = parse_markdown(doc).blocks

    assert [block.kind for block in blocks] == [BlockKind.TABLE, BlockKind.PARAGRAPH]
    assert blocks[0].text == "Name | Value\n--- | ---\nA | 1\nB | 2"


def test_indented_code_and_math_are_passthrough():
    doc = "    code line 1\n    code line 2\n\n$$\na + b\n$$\n\nText.\n"
    blocks = parse_markdown(doc).blocks

    assert [block.kind for block in blocks] == [
        BlockKind.CODE,
        BlockKind.MATH,
        BlockKind.PARAGRAPH,
    ]
    assert blocks[0].number == 0
    assert blocks[1].number == 0
    assert blocks[2].number == 1


def test_blockquote_lines_stay_together():
    doc = "> first line\n> second line\n\nText.\n"
    blocks = parse_markdown(doc).blocks

    assert [block.kind for block in blocks] == [BlockKind.BLOCKQUOTE, BlockKind.PARAGRAPH]
    assert blocks[0].text == "> first line\n> second line"


def test_blank_lines_roundtrip_through_parser():
    doc = "---\ntitle: Guide\n---\n\n# Title\n\nParagraph.\n\n\n\n- item\n\nEnd.\n"
    document = parse_markdown(doc)
    rebuilt = "".join(
        block.gap + block.text for block in document.blocks
    ) + document.trailer
    assert rebuilt == doc


def test_crlf_is_normalized_by_parser():
    doc = "# Title\r\n\r\nParagraph.\r\n"
    document = parse_markdown(doc)
    assert document.trailer == "\n"
    assert all("\r" not in block.text for block in document.blocks)
