"""双语对照排版。

两种版式：
- side_by_side：左右两列，逐块成对，块内 break-inside: avoid，避免分页错位/裁切
- stacked：原文块在上（弱化样式）、译文块在下，成对包裹，不跨页断裂

对齐策略：以原文块为骨架，译文按块类型与顺序贪心匹配；译文缺失时留空占位，
不改变原文顺序，因此分页后每个原文块仍对应自己的译文块。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..translation.markdown_parser import BlockKind, parse_markdown
from .html import escape_html, md_to_html

logger = logging.getLogger("m2p.bilingual")

_HTML_BLOCK_KINDS = frozenset(
    {BlockKind.HEADING, BlockKind.PARAGRAPH, BlockKind.LIST, BlockKind.TABLE, BlockKind.BLOCKQUOTE}
)

MISSING_BLOCK_HTML = '<p class="m2p-missing">（本块未翻译，已保留原文）</p>'

# 块与块之间的哨兵：pandoc 会把 HTML 注释原样输出，用它把整篇 HTML 切回逐块 HTML
BLOCK_SENTINEL = "<!--M2P-BLOCK-SPLIT-->"
_SENTINEL_RE = re.compile(r"<!--\s*M2P-BLOCK-SPLIT\s*-->")


def _render_blocks(blocks: list[str], render_block: Callable[[str], str]) -> list[str]:
    """一次 pandoc 调用渲染整篇，再按哨兵切回逐块 HTML。

    切分失败（标记被吞/数量不符）时退化为逐块渲染，保证正确性优先。
    """
    if not blocks:
        return []
    if len(blocks) == 1:
        return [render_block(blocks[0]) if blocks[0].strip() else ""]
    joined = ("\n\n" + BLOCK_SENTINEL + "\n\n").join(block for block in blocks)
    try:
        html = render_block(joined)
    except Exception:
        return [render_block(block) if block.strip() else "" for block in blocks]
    parts = _SENTINEL_RE.split(html)
    if len(parts) != len(blocks):
        return [render_block(block) if block.strip() else "" for block in blocks]
    return [part.strip() for part in parts]


@dataclass
class Pair:
    original: str
    translated: str
    kind: BlockKind


def pair_blocks(original_md: str, translated_md: str) -> list[Pair]:
    """按结构块配对原文与译文。"""
    original_blocks = [b for b in parse_markdown(original_md).blocks if b.kind in _HTML_BLOCK_KINDS]
    translated_blocks = [b for b in parse_markdown(translated_md).blocks if b.kind in _HTML_BLOCK_KINDS]
    pairs: list[Pair] = []
    cursor = 0
    for block in original_blocks:
        match_index = None
        for index in range(cursor, len(translated_blocks)):
            candidate = translated_blocks[index]
            if candidate.kind is block.kind and (
                block.kind is not BlockKind.HEADING or candidate.level == block.level
            ):
                match_index = index
                break
        if match_index is None:
            pairs.append(Pair(original=block.text, translated="", kind=block.kind))
            continue
        pairs.append(Pair(original=block.text, translated=translated_blocks[match_index].text, kind=block.kind))
        cursor = match_index + 1
    return pairs


def build_bilingual_html(
    original_md: str,
    translated_md: str,
    md_file_dir: Path,
    *,
    layout: str = "side_by_side",
    code_theme: str = "kate",
    target_language: str = "",
    original_label: str = "原文",
    translated_label: str = "译文",
    renderer: Callable[..., str] | None = None,
) -> str:
    """把原文/译文拼成双语 HTML 片段。"""
    render = renderer or md_to_html
    pairs = pair_blocks(original_md, translated_md)
    if not pairs:
        return ""

    def render_block(value: str) -> str:
        try:
            return render(value, md_file_dir, code_theme, target_language=target_language)
        except TypeError:  # 兼容仅接受三个位置参数的 renderer
            return render(value, md_file_dir, code_theme)

    # 整篇一次性交给 pandoc，再用 HTML 注释标记切回块：
    # 逐个块调用 pandoc 在大文档上会明显拖慢（每块一次进程/解析开销）。
    original_html = _render_blocks([p.original for p in pairs], render_block)
    translated_html = _render_blocks([p.translated for p in pairs], render_block)

    def to_html(text: str, index: int, side: list[str]) -> str:
        if not (text or "").strip():
            return ""
        return side[index]

    if layout == "stacked":
        parts = ['<section class="m2p-bilingual m2p-bilingual-stack">']
        for index, pair in enumerate(pairs):
            parts.append('<div class="m2p-pair">')
            parts.append(
                f'<div class="m2p-original"><div class="m2p-bilingual-label">{escape_html(original_label)}</div>'
                f"{to_html(pair.original, index, original_html)}</div>"
            )
            parts.append(
                f'<div class="m2p-translated"><div class="m2p-bilingual-label">{escape_html(translated_label)}</div>'
                f"{to_html(pair.translated, index, translated_html) or MISSING_BLOCK_HTML}</div>"
            )
            parts.append("</div>")
        parts.append("</section>")
        return "".join(parts)

    # 并排版式用 float 两列，而非嵌套表格：
    #  - 表格单元格里再放表格时 WeasyPrint 排版开销极大（大文档会显著变慢）
    #  - 行容器 break-inside: avoid 保证分页不会把一对原文/译文拆开
    rows = []
    for index, pair in enumerate(pairs):
        rows.append(
            '<div class="m2p-row">'
            f'<div class="m2p-col m2p-cell-original">{to_html(pair.original, index, original_html)}</div>'
            f'<div class="m2p-col m2p-cell-translated">'
            f"{to_html(pair.translated, index, translated_html) or MISSING_BLOCK_HTML}</div>"
            "</div>"
        )
    return f'<div class="m2p-bilingual m2p-bilingual-side">{"".join(rows)}</div>'