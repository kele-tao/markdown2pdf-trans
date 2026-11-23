"""双语对照排版。

两种版式：
- side_by_side：左右两列，逐块成对，块内 break-inside: avoid，避免分页错位/裁切
- stacked：原文块在上（弱化样式）、译文块在下，成对包裹，不跨页断裂

对齐策略：以原文块为骨架，译文按块类型与顺序贪心匹配；译文缺失时留空占位，
不改变原文顺序，因此分页后每个原文块仍对应自己的译文块。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..translation.markdown_parser import BlockKind, parse_markdown
from .html import escape_html, md_to_html

logger = logging.getLogger("m2p.bilingual")

_HTML_BLOCK_KINDS = frozenset(
    {BlockKind.HEADING, BlockKind.PARAGRAPH, BlockKind.LIST, BlockKind.TABLE, BlockKind.BLOCKQUOTE}
)


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

    def to_html(text: str) -> str:
        if not (text or "").strip():
            return ""
        try:
            return render(text, md_file_dir, code_theme, target_language=target_language)
        except TypeError:  # 兼容仅接受三个位置参数的 renderer
            return render(text, md_file_dir, code_theme)

    if layout == "stacked":
        parts = ['<section class="m2p-bilingual m2p-bilingual-stack">']
        for pair in pairs:
            parts.append('<div class="m2p-pair">')
            parts.append(
                f'<div class="m2p-original"><div class="m2p-bilingual-label">{escape_html(original_label)}</div>'
                f"{to_html(pair.original)}</div>"
            )
            parts.append(
                f'<div class="m2p-translated"><div class="m2p-bilingual-label">{escape_html(translated_label)}</div>'
                f"{to_html(pair.translated) or '<p class="m2p-missing">（本块未翻译，已保留原文）</p>'}</div>"
            )
            parts.append("</div>")
        parts.append("</section>")
        return "".join(parts)

    rows: list[str] = []
    for pair in pairs:
        rows.append(
            "<tr>"
            f'<td class="m2p-cell-original">{to_html(pair.original)}</td>'
            f'<td class="m2p-cell-translated">{to_html(pair.translated)}</td>'
            "</tr>"
        )
    return (
        '<table class="m2p-bilingual-side"><colgroup><col style="width:50%"><col style="width:50%"></colgroup>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )
