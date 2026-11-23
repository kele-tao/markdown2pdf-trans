"""Markdown 结构解析。

把文档拆成不可再分的结构块，记录块间空行、标题层级与标题路径，供切分、
结构校验与顺序拼回共用。代码块、表格、front matter 等结构在此层保持完整。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class BlockKind(str, Enum):
    FRONT_MATTER = "front_matter"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    BLOCKQUOTE = "blockquote"
    HR = "hr"
    MATH = "math"


TRANSLATABLE_KINDS = frozenset(
    {
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.LIST,
        BlockKind.TABLE,
        BlockKind.BLOCKQUOTE,
    }
)

BLOCK_LABELS: dict[BlockKind, str] = {
    BlockKind.HEADING: "标题",
    BlockKind.PARAGRAPH: "段落",
    BlockKind.LIST: "列表",
    BlockKind.TABLE: "表格",
    BlockKind.BLOCKQUOTE: "引用",
    BlockKind.CODE: "代码块",
    BlockKind.FRONT_MATTER: "文档元信息",
    BlockKind.HR: "分隔线",
    BlockKind.MATH: "独立公式",
}

_FENCE_RE = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})(?:[ \t]+(?P<text>.*?))?[ \t]*$")
_HR_RE = re.compile(r"^ {0,3}(?P<ch>[-*_])(?:[ \t]*(?P=ch)){2,}[ \t]*$")
_LIST_RE = re.compile(r"^ {0,3}(?P<marker>[-*+]|\d{1,9}[.)])(?:[ \t]+|$)")
_QUOTE_RE = re.compile(r"^ {0,3}>")
_TABLE_DELIM_RE = re.compile(
    r"^ {0,3}\|?[ \t]*:?-{1,}:?[ \t]*(?:\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$"
)
_SETEXT_RE = re.compile(r"^ {0,3}(?P<ch>=+|-{2,})[ \t]*$")
_INDENTED_RE = re.compile(r"^(?: {4}|\t)")


@dataclass
class Block:
    """文档中的一个结构块。

    - gap: 该块之前需要原样保留的空行（含上一块行尾换行），用于拼回时保持原始分隔。
    - path: 该块所属的标题路径（H1 > H2 > H3）。
    - number: 可翻译块的序号（1 起）；不可翻译块为 0。
    """

    kind: BlockKind
    text: str
    level: int = 0
    gap: str = ""
    path: tuple[str, ...] = ()
    number: int = 0
    start_line: int = 0


@dataclass
class ParsedDocument:
    blocks: list[Block]
    trailer: str = ""
    title: str = ""
    summary: str = ""

    def translatable_blocks(self) -> list[Block]:
        return [b for b in self.blocks if b.kind in TRANSLATABLE_KINDS]


def heading_text(line: str) -> str:
    """取标题行或 setext 标题行的纯文本。"""
    stripped = line.strip()
    match = _HEADING_RE.match(stripped)
    if match:
        text = (match.group("text") or "").strip()
        return re.sub(r"[ \t]+#+[ \t]*$", "", text).strip()
    return re.sub(r"[ \t]+#+[ \t]*$", "", stripped).strip()


def parse_markdown(md: str, *, detect_front_matter: bool = True) -> ParsedDocument:
    """按 Markdown 结构切块。

    块类型：front matter / 标题 / 段落 / 列表 / 表格 / 围栏代码块 / 引用 / 分隔线。
    段落、列表、表格、代码块、引用、front matter 均为不可拆单元；只有超长段落
    由上层按句子边界二次切分。行尾统一规范为 LF。
    """
    text = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    if text == "":
        return ParsedDocument(blocks=[], trailer="", title="", summary="")

    lines = text.split("\n")
    total_lines = len(lines)
    blocks: list[Block] = []
    pending_blank = 0
    is_first = True
    heading_stack: list[tuple[int, str]] = []

    def emit(kind: BlockKind, block_text: str, level: int = 0, line_no: int = 0) -> None:
        nonlocal is_first, pending_blank
        gap = "\n" * (pending_blank + (0 if is_first else 1))
        path = tuple(item for _, item in heading_stack)
        blocks.append(
            Block(
                kind=kind,
                text=block_text,
                level=level,
                gap=gap,
                path=path,
                start_line=line_no,
            )
        )
        is_first = False
        pending_blank = 0

    index = 0
    if detect_front_matter and lines and lines[0].strip() == "---":
        end = _front_matter_end(lines)
        if end is not None:
            emit(BlockKind.FRONT_MATTER, "\n".join(lines[: end + 1]), line_no=1)
            index = end + 1

    while index < total_lines:
        line = lines[index]

        if not line.strip():
            pending_blank += 1
            index += 1
            continue

        # setext 标题：单行文本 + 下一行 === / ---
        if index + 1 < total_lines and _is_setext_heading(lines, index):
            underline = _SETEXT_RE.match(lines[index + 1])
            assert underline is not None
            level = 1 if underline.group("ch").startswith("=") else 2
            raw = line.rstrip() + "\n" + lines[index + 1].rstrip()
            value = heading_text(line)
            _push_heading(heading_stack, level, value)
            emit(BlockKind.HEADING, raw, level=level, line_no=index + 1)
            index += 2
            continue

        # 围栏代码块
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            end = _consume_fence(lines, index)
            emit(BlockKind.CODE, "\n".join(lines[index:end]), line_no=index + 1)
            index = end
            continue

        # ATX 标题
        atx = _HEADING_RE.match(line)
        if atx:
            level = len(atx.group("hashes"))
            _push_heading(heading_stack, level, heading_text(line))
            emit(BlockKind.HEADING, line.rstrip(), level=level, line_no=index + 1)
            index += 1
            continue

        # 分隔线
        if _HR_RE.match(line):
            emit(BlockKind.HR, line.rstrip(), line_no=index + 1)
            index += 1
            continue

        # 独立公式块（$$ ... $$）原样保留
        if line.lstrip().startswith("$$"):
            end = index
            if line.lstrip()[2:].find("$$") >= 0:
                end = index + 1
            else:
                end = index + 1
                while end < total_lines and "$$" not in lines[end]:
                    end += 1
                if end < total_lines:
                    end += 1
            emit(BlockKind.MATH, "\n".join(lines[index:end]), line_no=index + 1)
            index = end
            continue

        # 表格：当前行含 | 且下一行为分隔行
        if _is_table_start(lines, index):
            end = index + 2
            while end < total_lines and lines[end].strip() and "|" in lines[end]:
                end += 1
            emit(BlockKind.TABLE, "\n".join(lines[index:end]), line_no=index + 1)
            index = end
            continue

        # 引用块
        if _QUOTE_RE.match(line):
            end = index
            while end < total_lines and _QUOTE_RE.match(lines[end]):
                end += 1
            emit(BlockKind.BLOCKQUOTE, "\n".join(lines[index:end]), line_no=index + 1)
            index = end
            continue

        # 列表
        if _LIST_RE.match(line):
            end = _consume_list(lines, index)
            emit(BlockKind.LIST, "\n".join(lines[index:end]), line_no=index + 1)
            index = end
            continue

        # 缩进代码块（顶层四空格 / Tab）
        if _INDENTED_RE.match(line):
            end = index
            while end < total_lines and (
                not lines[end].strip() or _INDENTED_RE.match(lines[end])
            ):
                end += 1
            while end > index and not lines[end - 1].strip():
                end -= 1
            emit(BlockKind.CODE, "\n".join(lines[index:end]), line_no=index + 1)
            index = end
            continue

        # 普通段落
        end = index + 1
        while end < total_lines and lines[end].strip() and not _starts_new_block(lines, end):
            end += 1
        emit(BlockKind.PARAGRAPH, "\n".join(lines[index:end]), line_no=index + 1)
        index = end

    trailer = "\n" * pending_blank
    number = 0
    for block in blocks:
        if block.kind in TRANSLATABLE_KINDS:
            number += 1
            block.number = number

    title = _document_title(blocks)
    summary = _document_summary(blocks, title)
    return ParsedDocument(blocks=blocks, trailer=trailer, title=title, summary=summary)


def extract_fenced_blocks(text: str) -> list[tuple[str, str]]:
    """提取文本中的全部围栏代码块，返回 (info, 代码内容) 列表。

    用于结构校验：即使代码块嵌套在列表里也能被识别并逐一比对。
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        match = _FENCE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        fence = match.group("fence")
        char = fence[0]
        length = len(fence)
        info = (match.group("info") or "").strip()
        body: list[str] = []
        index += 1
        while index < len(lines):
            close = re.match(
                r"^ {0,3}(" + re.escape(char) + "{" + str(length) + r",})[ \t]*$",
                lines[index],
            )
            if close:
                index += 1
                break
            body.append(lines[index])
            index += 1
        result.append((info, "\n".join(body)))
    return result


def plain_text(markdown_text: str) -> str:
    """去掉常见的行内标记，得到用于摘要的纯文本。"""
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", markdown_text or "")
    value = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"[*_~>#|]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _front_matter_end(lines: list[str]) -> int | None:
    limit = min(len(lines), 500)
    for index in range(1, limit):
        if lines[index].strip() in ("---", "..."):
            if not _looks_like_front_matter(lines[1:index]):
                return None
            return index
    return None


def _looks_like_front_matter(body: list[str]) -> bool:
    """front matter 至少包含一个 YAML 键值行，避免把开头的水平线误判为元信息。"""
    for line in body:
        if re.match(r"^[A-Za-z0-9_.-]+[ \t]*:", line):
            return True
    return False


def _push_heading(stack: list[tuple[int, str]], level: int, value: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, value))


def _is_setext_heading(lines: list[str], index: int) -> bool:
    if index > 0 and lines[index - 1].strip():
        return False
    if not _SETEXT_RE.match(lines[index + 1]):
        return False
    line = lines[index]
    if _FENCE_RE.match(line) or _HEADING_RE.match(line) or _LIST_RE.match(line):
        return False
    if _HR_RE.match(line) or _QUOTE_RE.match(line) or _INDENTED_RE.match(line):
        return False
    if _is_table_start(lines, index):
        return False
    return bool(line.strip())


def _consume_fence(lines: list[str], start: int) -> int:
    match = _FENCE_RE.match(lines[start])
    assert match is not None
    fence = match.group("fence")
    char = fence[0]
    length = len(fence)
    index = start + 1
    while index < len(lines):
        close = re.match(
            r"^ {0,3}(" + re.escape(char) + "{" + str(length) + r",})[ \t]*$",
            lines[index],
        )
        if close:
            return index + 1
        index += 1
    return len(lines)


def _consume_list(lines: list[str], start: int) -> int:
    index = start
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip():
            lookahead = index
            while lookahead < total and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < total and (
                _LIST_RE.match(lines[lookahead]) or _INDENTED_RE.match(lines[lookahead])
            ):
                index = lookahead
                continue
            break
        if index == start or _LIST_RE.match(line) or _INDENTED_RE.match(line):
            index += 1
            continue
        break
    return index


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    if "|" not in lines[index]:
        return False
    return bool(_TABLE_DELIM_RE.match(lines[index + 1]))


def _starts_new_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    if _FENCE_RE.match(line) or _HEADING_RE.match(line):
        return True
    if _HR_RE.match(line) or _QUOTE_RE.match(line) or _LIST_RE.match(line):
        return True
    return _is_table_start(lines, index)


def _document_title(blocks: list[Block]) -> str:
    for block in blocks:
        if block.kind is BlockKind.HEADING and heading_text(block.text):
            return heading_text(block.text)
    return ""


def _document_summary(blocks: list[Block], title: str) -> str:
    paragraph = next((b for b in blocks if b.kind is BlockKind.PARAGRAPH), None)
    snippet = plain_text(paragraph.text)[:200] if paragraph else ""
    snippet = re.sub(r"\s+", " ", snippet).strip()
    if title and snippet and snippet != title:
        return f"《{title}》{snippet}"
    if title:
        return f"《{title}》"
    return snippet
