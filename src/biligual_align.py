"""
双语对照分块：按 Markdown 结构（标题 / 列表 / 段落 / 代码块）切块。
每个块保留类型信息，渲染时按块配对，避免 AI 改个空行就错位。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    CODE = "code"
    BLOCKQUOTE = "blockquote"
    TABLE = "table"
    HR = "hr"
    BLANK = "blank"


@dataclass
class Block:
    type: BlockType
    content: str
    level: int = 0  # heading level


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^(```|~~~)")


def split_markdown_to_blocks(md: str) -> list[Block]:
    """结构化分块。保留代码块/表格/列表为整体，不拆散。"""
    lines = md.splitlines()
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 空行：合并连续的
        if not stripped:
            j = i
            while j < n and not lines[j].strip():
                j += 1
            blocks.append(Block(BlockType.BLANK, "\n".join(lines[i:j])))
            i = j
            continue

        # 围栏代码块
        if _FENCE_RE.match(stripped):
            fence = re.match(r"^(`{3,}|~{3,})", stripped).group(1)
            j = i + 1
            while j < n:
                if lines[j].strip().startswith(fence):
                    j += 1
                    break
                j += 1
            blocks.append(Block(BlockType.CODE, "\n".join(lines[i:j])))
            i = j
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+", line)
        if m:
            blocks.append(Block(BlockType.HEADING, line, level=len(m.group(1))))
            i += 1
            continue

        # 水平线
        if re.match(r"^[-*_]{3,}\s*$", stripped):
            blocks.append(Block(BlockType.HR, line))
            i += 1
            continue

        # 引用块（连续 > 开头的行）
        if stripped.startswith(">"):
            j = i
            while j < n and (lines[j].strip().startswith(">") or lines[j].strip() == ""):
                if lines[j].strip() == "" and (j + 1 >= n or not lines[j + 1].strip().startswith(">")):
                    break
                j += 1
            blocks.append(Block(BlockType.BLOCKQUOTE, "\n".join(lines[i:j])))
            i = j
            continue

        # 列表（连续 -/*/+ 或数字. 开头的行）
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            j = i
            while j < n and (
                re.match(r"^\s*([-*+]|\d+\.)\s+", lines[j]) or lines[j].strip() == "" or lines[j].startswith("  ")
            ):
                if lines[j].strip() == "" and (j + 1 >= n or not re.match(r"^\s*([-*+]|\d+\.)\s+", lines[j + 1] or "")):
                    break
                j += 1
            blocks.append(Block(BlockType.LIST, "\n".join(lines[i:j])))
            i = j
            continue

        # 表格（| 开头的连续行）
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1]):
            j = i
            while j < n and "|" in lines[j]:
                j += 1
            blocks.append(Block(BlockType.TABLE, "\n".join(lines[i:j])))
            i = j
            continue

        # 普通段落（到下一个空行/特殊行为止）
        j = i
        while j < n and lines[j].strip() and not re.match(r"^(#{1,6}\s|>|```|~~~|\s*[-*+]\s|\s*\d+\.\s)", lines[j]):
            j += 1
        blocks.append(Block(BlockType.PARAGRAPH, "\n".join(lines[i:j])))
        i = j

    # 合并连续的 BLANK
    merged: list[Block] = []
    for b in blocks:
        if b.type == BlockType.BLANK and merged and merged[-1].type == BlockType.BLANK:
            continue
        merged.append(b)
    # 去掉首尾空块
    while merged and merged[0].type == BlockType.BLANK:
        merged.pop(0)
    while merged and merged[-1].type == BlockType.BLANK:
        merged.pop()
    return merged


def align_blocks(original: list[Block], translated: list[Block]) -> list[tuple[Block, Block]]:
    """
    把原文/译文按结构对齐。
    策略：按 type + 顺序匹配。如果 AI 漏译/多译，缺的用空 block 补齐。
    """
    aligned: list[tuple[Block, Block]] = []
    ti = 0
    for ob in original:
        if ob.type == BlockType.BLANK:
            # 空行在译文里找个最近的空行；找不到就插空
            while ti < len(translated) and translated[ti].type != BlockType.BLANK:
                # 译文少了一个空行？补一个
                aligned.append((ob, Block(BlockType.BLANK, "")))
                break
            else:
                if ti < len(translated):
                    aligned.append((ob, translated[ti]))
                    ti += 1
                else:
                    aligned.append((ob, Block(BlockType.BLANK, "")))
            continue

        # 找下一个匹配的 type
        matched = False
        for k in range(ti, len(translated)):
            if translated[k].type == ob.type:
                aligned.append((ob, translated[k]))
                ti = k + 1
                matched = True
                break
        if not matched:
            # 译文缺这个块，插空
            aligned.append((ob, Block(BlockType.BLANK, "")))
    return aligned
