"""超长段落切分。

段落是唯一允许二次切分的块：按句子边界切到 max_chars 以内，切分点避开
行内代码、链接地址、公式与占位符。其余结构（列表、表格、代码块、引用）保持完整。
"""
from __future__ import annotations

import re

from .markdown_parser import Block, BlockKind

_COMPOUND_SUFFIX = r"[\"'”’」』）)\]】]*"
_SENTENCE_RE = re.compile(r"[.!?…。！？]+" + _COMPOUND_SUFFIX)

_PROTECTED_PATTERNS = (
    re.compile(r"`+[^`\n]*`+"),
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"\[[^\]]*\]\([^)]*\)"),
    re.compile(r"\$\$[\s\S]*?\$\$"),
    re.compile(r"(?<!\$)\$(?!\$)[^$\n]+\$(?!\$)"),
    re.compile(r"<https?://[^>\s]+>"),
    re.compile(r"\{\{[\s\S]*?\}\}"),
    re.compile(r"\$\{[^{}\n]*\}"),
)

_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "fig",
        "no",
        "al",
        "inc",
        "ltd",
        "co",
        "dept",
        "vol",
        "pp",
        "sec",
    }
)

_CJK_LANGUAGES = frozenset(
    {"chinese", "中文", "汉语", "简体中文", "繁体中文", "japanese", "日语", "korean", "韩语"}
)


def is_cjk_language(language: str) -> bool:
    value = (language or "").strip().casefold()
    return any(token in value for token in _CJK_LANGUAGES)


def split_block_segments(block: Block, max_chars: int) -> list[str]:
    """返回该块需要翻译的文本片段；非段落或未超限时返回单段。"""
    limit = max(1, int(max_chars or 1))
    text = block.text
    if block.kind is not BlockKind.PARAGRAPH:
        return [text]
    if len(text) <= limit:
        return [text]
    return split_long_text(text, limit)


def split_long_text(text: str, max_chars: int) -> list[str]:
    """按句子边界把长文本切成不超过 max_chars 的片段。"""
    limit = max(1, int(max_chars or 1))
    stripped = (text or "").strip()
    if not stripped:
        return [text]
    if len(stripped) <= limit:
        return [stripped]

    pieces = _sentence_pieces(stripped)
    segments: list[str] = []
    current = ""

    for piece in pieces:
        if not piece:
            continue
        candidate = current + piece if current else piece
        if current and len(candidate.strip()) > limit:
            segments.append(current.strip())
            current = piece
        else:
            current = candidate

        while len(current.strip()) > limit:
            cut = _safe_cut(current, limit)
            segments.append(current[:cut].strip())
            current = current[cut:]

    if current.strip():
        segments.append(current.strip())
    return [segment for segment in segments if segment]


def join_segments(parts: list[str], target_language: str) -> str:
    """把分段译文拼回一个段落，CJK 目标语言避免句间多余空格。"""
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]

    cjk = is_cjk_language(target_language)
    result = cleaned[0]
    for part in cleaned[1:]:
        last = result[-1]
        first = part[0]
        if cjk and (last in "。！？；：」』）】" or (_is_cjk(last) and _is_cjk(first))):
            result += part
        else:
            result += " " + part
    return result


def _sentence_pieces(text: str) -> list[str]:
    spans = _protected_spans(text)
    pieces: list[str] = []
    last = 0
    for match in _SENTENCE_RE.finditer(text):
        start, end = match.start(), match.end()
        if _inside(start, spans) or _inside(end, spans):
            continue
        if match.group(0)[0] not in "。！？…":
            if end < len(text) and not text[end].isspace():
                continue
            if _is_abbreviation(text, start):
                continue
        pieces.append(text[last:end])
        last = end
    if last < len(text):
        pieces.append(text[last:])
    return [piece for piece in pieces if piece.strip()]


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in _PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            if match.end() > match.start():
                spans.append((match.start(), match.end()))
    spans.sort()
    return spans


def _inside(position: int, spans: list[tuple[int, int]]) -> bool:
    for start, end in spans:
        if start >= position:
            return False
        if start < position < end:
            return True
    return False


def _is_abbreviation(text: str, punct_position: int) -> bool:
    match = re.search(r"([A-Za-z][A-Za-z.]*)$", text[:punct_position])
    if not match:
        return False
    word = match.group(1).lower().rstrip(".")
    return word in _ABBREVIATIONS


def _safe_cut(text: str, limit: int) -> int:
    """在 limit 之前找一个不破坏受保护片段的切分点，返回切片长度。"""
    cut = min(limit, len(text))
    for start, _end in _protected_spans(text):
        if start < cut < _end:
            cut = start
            break
    if cut <= 0:
        cut = min(limit, len(text))
    if cut <= 0:
        return 1
    whitespace = max(
        text.rfind(" ", 0, cut),
        text.rfind("\n", 0, cut),
        text.rfind("\t", 0, cut),
    )
    if whitespace > 0 and whitespace >= cut - 200:
        return whitespace + 1
    return cut


def _is_cjk(char: str) -> bool:
    return bool(char) and (
        "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff"
    )
