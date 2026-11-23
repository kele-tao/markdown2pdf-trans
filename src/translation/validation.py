"""译文格式保真校验。

在块级别比对原文与译文的 Markdown 结构：块类型、标题级别、围栏代码块内容、
行内代码、链接与图片地址数量、表格列数与行数、列表项数量、公式与占位符。
校验不通过即触发重试；重试仍失败时由上层保留原文并告警。
"""
from __future__ import annotations

import re

from .markdown_parser import (
    BLOCK_LABELS,
    BlockKind,
    extract_fenced_blocks,
    heading_text,
    parse_markdown,
)

_INLINE_CODE_RE = re.compile(r"(?<!`)`+([^`\n]+?)`+(?!`)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(\s*([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_AUTOLINK_RE = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_REF_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
_BLOCK_MATH_RE = re.compile(r"\$\$([\s\S]+?)\$\$")
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)([^\n$]+?)\$(?!\$)")
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{[^{}\n]+\}\}"),
    re.compile(r"\$\{[^{}\n]+\}"),
    re.compile(r"%[sdifr]"),
    re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}"),
)
_LIST_ITEM_RE = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+", re.MULTILINE)

_EXPECTED_SINGLE_KIND = {
    BlockKind.PARAGRAPH: BlockKind.PARAGRAPH,
    BlockKind.LIST: BlockKind.LIST,
    BlockKind.TABLE: BlockKind.TABLE,
    BlockKind.BLOCKQUOTE: BlockKind.BLOCKQUOTE,
}


def validate_translation(
    original_text: str,
    translated_text: str,
    kind: BlockKind,
    level: int = 0,
) -> list[str]:
    """返回校验问题列表；空列表表示结构一致。"""
    text = (translated_text or "").strip()
    if not text:
        return ["译文为空"]

    issues: list[str] = []
    parsed = parse_markdown(text, detect_front_matter=False).blocks

    if kind is BlockKind.HEADING:
        if len(parsed) != 1 or parsed[0].kind is not BlockKind.HEADING:
            issues.append("译文不是单个标题块（原文为标题）")
        else:
            if parsed[0].level != level:
                issues.append(f"标题级别不一致（原文 {level} 级，译文 {parsed[0].level} 级）")
            if not heading_text(parsed[0].text):
                issues.append("标题内容为空")
    else:
        expected = _EXPECTED_SINGLE_KIND.get(kind)
        actual_kinds = [block.kind for block in parsed]
        if expected is None or actual_kinds != [expected]:
            actual_label = "、".join(BLOCK_LABELS.get(item, item.value) for item in actual_kinds) or "空"
            issues.append(
                f"块结构不一致（原文为{BLOCK_LABELS.get(kind, '文本')}，译文解析为{actual_label}）"
            )
        elif kind is BlockKind.TABLE:
            issues.extend(_check_table_shape(original_text, parsed[0].text))
        elif kind is BlockKind.LIST:
            issues.extend(_check_list_items(original_text, parsed[0].text))

    issues.extend(_check_fences(original_text, text))
    issues.extend(_check_inline_code(original_text, text))
    issues.extend(_check_links(original_text, text))
    issues.extend(_check_math(original_text, text))
    issues.extend(_check_placeholders(original_text, text))

    return _dedupe(issues)


def _check_fences(original: str, translated: str) -> list[str]:
    expected = extract_fenced_blocks(original)
    actual = extract_fenced_blocks(translated)
    if len(expected) != len(actual):
        return [f"围栏代码块数量不一致（原文 {len(expected)}，译文 {len(actual)}）"]
    issues: list[str] = []
    for index, ((expected_info, expected_body), (actual_info, actual_body)) in enumerate(
        zip(expected, actual), start=1
    ):
        if expected_body.strip("\n") != actual_body.strip("\n"):
            issues.append(f"第 {index} 个代码块内容被改动")
            break
        if expected_info.casefold() != actual_info.casefold():
            issues.append(f"第 {index} 个代码块语言标记被改动（{expected_info or '无'} → {actual_info or '无'}）")
            break
    return issues


def _check_inline_code(original: str, translated: str) -> list[str]:
    expected = sorted(_INLINE_CODE_RE.findall(original))
    if not expected:
        return []
    actual = sorted(_INLINE_CODE_RE.findall(translated))
    if expected == actual:
        return []
    missing = _difference(expected, actual)
    if missing:
        return [f"行内代码内容被改动或丢失（如 `{missing[0]}`）"]
    return ["行内代码数量或被改动"]


def _check_links(original: str, translated: str) -> list[str]:
    issues: list[str] = []
    expected_images = _IMAGE_RE.findall(original)
    actual_images = _IMAGE_RE.findall(translated)
    if len(expected_images) != len(actual_images):
        issues.append(f"图片数量不一致（原文 {len(expected_images)}，译文 {len(actual_images)}）")

    expected_links = _LINK_RE.findall(original)
    actual_links = _LINK_RE.findall(translated)
    if len(expected_links) != len(actual_links):
        issues.append(f"链接数量不一致（原文 {len(expected_links)}，译文 {len(actual_links)}）")

    expected_autolinks = _AUTOLINK_RE.findall(original)
    actual_autolinks = _AUTOLINK_RE.findall(translated)
    if len(expected_autolinks) != len(actual_autolinks):
        issues.append("自动链接数量不一致")

    expected_refs = _REF_DEF_RE.findall(original)
    if expected_refs and len(expected_refs) != len(_REF_DEF_RE.findall(translated)):
        issues.append("引用式链接定义数量不一致")

    for url in expected_images + expected_links + expected_autolinks + expected_refs:
        if url and url not in translated:
            issues.append(f"链接地址被改动或丢失（{_truncate(url)}）")
            break
    return issues


def _check_math(original: str, translated: str) -> list[str]:
    issues: list[str] = []
    expected_blocks = sorted(value.strip() for value in _BLOCK_MATH_RE.findall(original))
    if expected_blocks:
        actual_blocks = sorted(value.strip() for value in _BLOCK_MATH_RE.findall(translated))
        if expected_blocks != actual_blocks:
            issues.append("独立公式（$$...$$）内容被改动或丢失")

    expected_inline = sorted(
        value for value in _INLINE_MATH_RE.findall(original) if _looks_like_math(value)
    )
    if expected_inline:
        actual_inline = sorted(value for value in _INLINE_MATH_RE.findall(translated) if _looks_like_math(value))
        if expected_inline != actual_inline:
            issues.append("行内公式（$...$）内容被改动或丢失")
    return issues


def _check_placeholders(original: str, translated: str) -> list[str]:
    expected = _placeholders(original)
    if not expected:
        return []
    actual = _placeholders(translated)
    if sorted(expected) == sorted(actual):
        return []
    missing = _difference(sorted(expected), sorted(actual))
    if missing:
        return [f"占位符被改动或丢失（如 {missing[0]}）"]
    return ["占位符数量或被改动"]


def _check_table_shape(original_table: str, translated_table: str) -> list[str]:
    expected_columns, expected_rows = _table_shape(original_table)
    actual_columns, actual_rows = _table_shape(translated_table)
    issues: list[str] = []
    if expected_columns != actual_columns:
        issues.append(f"表格列数不一致（原文 {expected_columns}，译文 {actual_columns}）")
    if expected_rows != actual_rows:
        issues.append(f"表格行数不一致（原文 {expected_rows}，译文 {actual_rows}）")
    return issues


def _check_list_items(original_list: str, translated_list: str) -> list[str]:
    expected = len(_LIST_ITEM_RE.findall(original_list))
    actual = len(_LIST_ITEM_RE.findall(translated_list))
    if expected != actual:
        return [f"列表项数量不一致（原文 {expected}，译文 {actual}）"]
    return []


def _table_shape(table_text: str) -> tuple[int, int]:
    rows = [line.strip() for line in table_text.split("\n") if line.strip()]
    if not rows:
        return (0, 0)
    header = rows[0].strip()
    if header.startswith("|"):
        header = header[1:]
    if header.endswith("|"):
        header = header[:-1]
    columns = len([cell for cell in header.split("|")])
    return (columns, len(rows))


def _placeholders(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _PLACEHOLDER_PATTERNS:
        found.extend(pattern.findall(text))
    return found


def _looks_like_math(value: str) -> bool:
    stripped = value.strip()
    if not stripped or any(char.isspace() for char in stripped):
        return False
    return bool(re.search(r"[\\^_=+{}<>]", stripped))


def _difference(expected: list[str], actual: list[str]) -> list[str]:
    remaining = list(actual)
    missing: list[str] = []
    for item in expected:
        if item in remaining:
            remaining.remove(item)
        else:
            missing.append(item)
    return missing


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _truncate(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[:limit] + "…"
