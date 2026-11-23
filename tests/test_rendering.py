"""排版测试：主题、封面、目录页码、页眉页脚、RTL、双语版式、大文档稳定性。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from src.config import THEME_PRESETS, is_rtl_language, resolve_theme
from src.rendering.bilingual import build_bilingual_html, pair_blocks
from src.rendering.css import get_css_style, merge_style, page_size_css
from src.rendering.html import (
    add_heading_ids,
    build_cover_html,
    build_toc_html,
    extract_headings,
    preprocess_markdown_images,
)
from src.renderer import render_bilingual_to_pdf, render_markdown_to_pdf

B = chr(96) * 3

MD = (
    "# 安装指南\n\n这是介绍段落。\n\n## 环境要求\n\n- 内存 8 GB\n- 磁盘 20 GB\n\n"
    "| 组件 | 版本 |\n| --- | --- |\n| Python | 3.12 |\n\n"
    + B + "bash\npip install demo\n" + B + "\n\n> 注意：先备份。\n\n公式 $E=mc^2$。\n"
)


def _pages(pdf: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf)).pages)


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(pdf)).pages)


# ------------------------------------------------------------------ 主题与样式
def test_all_themes_are_complete():
    required = {
        "name", "text_color", "heading_color", "link_color", "font_family",
        "font_size", "line_height", "page_size", "page_margin", "code_bg",
    }
    assert len(THEME_PRESETS) >= 4
    for key, theme in THEME_PRESETS.items():
        missing = required - set(theme)
        assert not missing, f"主题 {key} 缺少 {missing}"


def test_unknown_theme_falls_back_to_default():
    assert resolve_theme("does-not-exist")["key"] == "default"


def test_css_respects_page_size_orientation_margin_and_counters():
    style = merge_style({"theme": "academic", "page_size": "Letter", "page_orientation": "landscape", "page_margin": "1.5cm"})
    css = get_css_style(style, document={"page_number": True, "header": True, "toc": True, "cover": True}, target_language="English")
    assert "size: Letter landscape" in css
    assert "margin: 1.5cm" in css
    assert "counter(page)" in css and "counter(pages)" in css
    assert "target-counter" in css
    assert "@page cover" in css
    assert "display: table-header-group" in css
    assert "break-inside: avoid" in css


def test_rtl_language_sets_direction_and_alignment():
    css = get_css_style(merge_style({"theme": "default"}), target_language="Arabic")
    assert "direction: rtl" in css
    assert "text-align: right" in css
    assert "Noto Naskh Arabic" in css
    assert is_rtl_language("Arabic") and not is_rtl_language("English")


def test_page_size_helper_normalizes_orientation():
    assert page_size_css({"page_size": "A4", "page_orientation": "landscape"}) == "A4 landscape"
    assert page_size_css({"page_size": "A5", "page_orientation": "sideways"}) == "A5 portrait"

# ------------------------------------------------------------------ 目录 / 封面
def test_extract_headings_skips_code_fences_and_sets_ids():
    md = "# A\n\n" + B + "\n# not a heading\n" + B + "\n\n## B\n\n### C\n"
    headings = extract_headings(md)
    assert [item["text"] for item in headings] == ["A", "B", "C"]
    assert [item["level"] for item in headings] == [1, 2, 3]
    assert [item["id"] for item in headings] == ["m2p-h-1", "m2p-h-2", "m2p-h-3"]


def test_toc_html_links_to_heading_anchors():
    html = build_toc_html(extract_headings("# One\n\n## Two\n"))
    assert 'href="#m2p-h-1"' in html
    assert 'href="#m2p-h-2"' in html
    assert "toc-l2" in html


def test_add_heading_ids_is_positional_and_idempotent():
    """目录锚点必须按位置编号（与 extract_headings 一致），原 pandoc id 退到 data-anchor。"""
    html = "<h1>One</h1><h2 id='x'>Two</h2><h3 class='c'>Three</h3>"
    out = add_heading_ids(html)
    assert 'id="m2p-h-1"' in out and 'id="m2p-h-2"' in out and 'id="m2p-h-3"' in out
    assert 'data-anchor="x"' in out
    assert "class='c'" in out
    # 重复标题也必须拿到不同锚点（否则目录页码会漂移）
    dup = add_heading_ids("<h1>A</h1><h1>A</h1><h2>A</h2>")
    assert dup.count('id="m2p-h-') == 3
    # 再跑一次不会叠加 id
    assert add_heading_ids(out).count('id="m2p-h-1"') == 1


def test_cover_html_escapes_and_renders_meta():
    html = build_cover_html(title="<script>x</script>", subtitle="副标题", author="团队", date="2026-01-01")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "m2p-cover" in html and "cover-title" in html
    assert "团队" in html and "2026-01-01" in html


def test_cover_html_empty_when_no_title():
    assert build_cover_html(title="", subtitle="") == ""


# ------------------------------------------------------------------ 图片内联
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63000100000500010d0a2db4000000"
    "0049454e44ae426082"
)


def test_local_image_is_embedded_as_data_uri(tmp_path: Path):
    (tmp_path / "img.png").write_bytes(PNG_1PX)
    result = preprocess_markdown_images("![alt](img.png)", tmp_path)
    assert result.startswith("![alt](data:image/png;base64,")


def test_image_outside_directory_is_not_embedded(tmp_path: Path):
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"data")
    result = preprocess_markdown_images("![alt](../secret.png)", tmp_path)
    assert "data:image" not in result


# ------------------------------------------------------------------ PDF 产出
def test_render_with_cover_and_toc_produces_multiple_pages(tmp_path: Path):
    out = tmp_path / "doc.pdf"
    pdf = render_markdown_to_pdf(
        MD,
        tmp_path,
        out,
        {"theme": "academic"},
        document={"cover": True, "toc": True, "header": True, "footer": True, "page_number": True, "author": "Team"},
        target_language="English",
    )
    assert out.is_file() and out.read_bytes().startswith(b"%PDF")
    assert _pages(pdf) >= 3
    text = _text(pdf)
    assert "目录" in text or "Contents" in text


def test_render_without_cover_and_toc_is_shorter(tmp_path: Path):
    out = tmp_path / "plain.pdf"
    pdf = render_markdown_to_pdf(
        MD,
        tmp_path,
        out,
        {"theme": "modern"},
        document={"cover": False, "toc": False, "header": False, "page_number": False},
        target_language="English",
    )
    assert "目录" not in _text(pdf)
    assert _pages(pdf) >= 1


def test_render_arabic_rtl_document(tmp_path: Path):
    out = tmp_path / "ar.pdf"
    pdf = render_markdown_to_pdf(
        "# مرحبا بالعالم\n\nهذا نص تجريبي للعرض.\n\n## قسم\n\nمحتوى.\n",
        tmp_path,
        out,
        {"theme": "default"},
        document={"cover": True, "toc": True, "page_number": True},
        target_language="Arabic",
    )
    assert _pages(pdf) >= 2


def test_long_code_and_table_css_rules(tmp_path: Path):
    long_line = "print(" + ", ".join(f'"x{i}"' for i in range(60)) + ")"
    md = (
        "# T\n\n" + B + "python\n" + long_line + "\n" + B
        + "\n\n| a | b |\n| --- | --- |\n"
        + "\n".join(f"| {i} | value {i} |" for i in range(60))
    )
    out = tmp_path / "long.pdf"
    pdf = render_markdown_to_pdf(md, tmp_path, out, {"theme": "business"}, document={"cover": False, "toc": False})
    css = get_css_style({"theme": "business"})
    assert "white-space: pre-wrap" in css
    assert "display: table-header-group" in css
    assert _pages(pdf) >= 1

# ------------------------------------------------------------------ 双语
def test_pair_blocks_matches_by_kind():
    pairs = pair_blocks("# 标题\n\n段落一。\n\n- 项目\n", "# Title\n\nParagraph one.\n\n- item\n")
    assert [pair.kind.value for pair in pairs] == ["heading", "paragraph", "list"]
    assert pairs[0].translated.startswith("# Title")


def test_pair_blocks_tolerates_missing_translation():
    pairs = pair_blocks("# 标题\n\n段落一。\n", "# Title\n")
    assert len(pairs) == 2
    assert pairs[1].translated == ""


def test_bilingual_side_by_side_html_has_two_columns():
    html = build_bilingual_html("# 标题\n\n段落。\n", "# Title\n\nParagraph.\n", Path("."), layout="side_by_side")
    assert "m2p-bilingual-side" in html
    # 并排版式：2 个结构块 → 2 个 float 行 × 2 列（不用嵌套表格，避免 WeasyPrint 巨慢）
    assert html.count('class="m2p-row"') == 2
    assert html.count('class="m2p-col ') == 4
    assert "m2p-cell-original" in html and "m2p-cell-translated" in html
    assert "<table" not in html


def test_bilingual_stacked_html_has_pairs():
    html = build_bilingual_html("# 标题\n\n段落。\n", "# Title\n\nParagraph.\n", Path("."), layout="stacked")
    assert "m2p-bilingual-stack" in html
    assert html.count("m2p-pair") == 2


def test_bilingual_pdf_both_layouts_render(tmp_path: Path):
    original = "# 标题\n\n段落一。\n\n段落二。\n"
    translated = "# Title\n\nParagraph one.\n\nParagraph two.\n"
    for layout in ("side_by_side", "stacked"):
        out = tmp_path / f"bi-{layout}.pdf"
        pdf = render_bilingual_to_pdf(
            original, translated, tmp_path, out,
            {"theme": "default"},
            document={"cover": True, "toc": True},
            target_language="English",
            layout=layout,
        )
        assert out.is_file()
        assert _pages(pdf) >= 2, layout


def test_bilingual_css_prevents_page_breaks_inside_pairs():
    for layout in ("side_by_side", "stacked"):
        css = get_css_style({"theme": "default"}, bilingual_layout=layout)
        assert "m2p-bilingual" in css
        assert "break-inside: avoid" in css
