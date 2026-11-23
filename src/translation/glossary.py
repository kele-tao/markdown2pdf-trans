"""术语表：命中匹配、提示词注入、译文术语核对与候选术语自动提取。"""
from __future__ import annotations

import logging
import re
from typing import Callable

from .models import GlossaryEntry

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_MAX_DOC_CHARS_FOR_EXTRACTION = 20000

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "and",
        "but",
        "for",
        "with",
        "from",
        "when",
        "where",
        "while",
        "if",
        "in",
        "on",
        "at",
        "to",
        "of",
        "as",
        "by",
        "it",
        "is",
        "are",
        "be",
        "was",
        "were",
        "we",
        "you",
        "they",
        "he",
        "she",
        "our",
        "your",
        "their",
        "its",
        "note",
        "figure",
        "table",
        "chapter",
        "section",
        "example",
        "step",
        "first",
        "second",
        "third",
    }
)

_CJK_STOP_TERMS = frozenset(
    {
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "这些",
        "那些",
        "可以",
        "以及",
        "如果",
        "因为",
        "所以",
        "但是",
        "需要",
        "进行",
        "使用",
        "通过",
        "对于",
        "一个",
        "没有",
        "已经",
        "不是",
        "就是",
        "其中",
        "同时",
        "然后",
        "目前",
        "将会",
        "以下",
        "如下",
        "上述",
        "相关",
        "内容",
        "文档",
        "问题",
        "方式",
        "情况",
    }
)

_INLINE_CODE_RE = re.compile(r"`([^`\n]{2,40})`")
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAMEL_CASE_RE = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_CAPITALIZED_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[- ]+[A-Z][A-Za-z0-9]*){0,2}\b")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]{2,}")

_GLOSSARY_SYSTEM_TEMPLATE = """你是术语抽取助手。阅读用户给出的 Markdown 文档，抽取翻译成 {target_language} 时需要保持统一译法的关键术语。

规则：
1. 优先抽取产品名、专有名词、技术概念、领域短语；排除普通虚词、一般动词和泛化名词。
2. 只输出术语表，每行一条，格式为「原文<TAB>译文」，不要表头、不要解释、不要 Markdown 表格、不要代码围栏。
3. 最多输出 {max_terms} 行；无法确定译法时，译文可以保留原文。"""


def normalize_entries(entries: list[GlossaryEntry] | None) -> list[GlossaryEntry]:
    """去空、按原文去重（不区分大小写），保持传入顺序。"""
    result: list[GlossaryEntry] = []
    seen: set[str] = set()
    for entry in entries or []:
        source = re.sub(r"\s+", " ", (entry.source or "")).strip()
        target = re.sub(r"\s+", " ", (entry.target or "")).strip()
        if not source or not target or len(source) > 200 or len(target) > 200:
            continue
        key = source.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(GlossaryEntry(source=source, target=target))
    return result


def term_in_text(term: str, text: str) -> bool:
    """判断术语是否出现在文本中。

    拉丁术语按整词、不区分大小写匹配（避免 predeployment 命中 deployment）；
    CJK 术语按子串匹配（中文没有词边界）。
    """
    source = (term or "").strip()
    if not source or not text:
        return False
    if _CJK_RE.search(source):
        return source in text
    if not _LATIN_RE.search(source):
        return source in text
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(source) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def find_matching_entries(text: str, entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """返回在当前块文本中命中的术语条目（保持术语表顺序）。"""
    return [entry for entry in entries if term_in_text(entry.source, text)]


def verify_glossary_usage(translated: str, entries: list[GlossaryEntry]) -> list[str]:
    """核对命中术语的固定译法是否出现在译文中，缺失时返回中文告警。"""
    warnings: list[str] = []
    for entry in entries:
        if not _target_present(entry.target, translated):
            warnings.append(f"术语「{entry.source}」未按术语表译为「{entry.target}」")
    return warnings


def suggest_glossary(
    md: str,
    target_language: str,
    *,
    ai_call: Callable[[str, str], str] | None = None,
    max_terms: int = 40,
) -> list[GlossaryEntry]:
    """自动提取候选术语。

    优先由 AI 抽取（按术语表格式解析）；AI 不可用、调用失败或结果为空时，
    改用离线启发式抽取，保证主流程不受影响。返回条目供用户确认或修改。
    """
    limit = max(0, int(max_terms))
    text = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    if limit == 0 or not text.strip():
        return []

    if ai_call is not None:
        try:
            raw = ai_call(
                _GLOSSARY_SYSTEM_TEMPLATE.format(
                    target_language=(target_language or "").strip() or "目标语言",
                    max_terms=limit,
                ),
                _build_glossary_user_content(text),
            )
            entries = _parse_glossary_response(raw)
            entries = _filter_present(entries, text)
            entries = normalize_entries(entries)[:limit]
            if entries:
                return entries
        except Exception as exc:  # 术语提取失败绝不影响主流程
            logger.warning("术语自动提取失败，改用启发式提取：%s", exc)

    return heuristic_terms(text, limit)


def heuristic_terms(text: str, max_terms: int) -> list[GlossaryEntry]:
    """离线候选术语：行内代码、标识符、专有名词与高频中文短语。"""
    limit = max(0, int(max_terms))
    if limit == 0:
        return []

    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    cursor = 0

    def add(term: str, weight: float, count: int = 1) -> None:
        nonlocal cursor
        cleaned = _clean_term(term)
        if not cleaned:
            return
        if cleaned not in scores:
            scores[cleaned] = 0.0
            order[cleaned] = cursor
            cursor += 1
        scores[cleaned] += weight * count

    inline_codes = _INLINE_CODE_RE.findall(text)
    for term in inline_codes:
        add(term, 3.0)
    text_without_code = _INLINE_CODE_RE.sub(" ", text)

    for pattern, weight in (
        (_SNAKE_CASE_RE, 2.0),
        (_CAMEL_CASE_RE, 2.0),
    ):
        for match in pattern.finditer(text_without_code):
            add(match.group(0), weight)

    capitalized_counts: dict[str, int] = {}
    for match in _CAPITALIZED_RE.finditer(text_without_code):
        term = _clean_term(match.group(0))
        if not term or _is_stop_phrase(term):
            continue
        capitalized_counts[term] = capitalized_counts.get(term, 0) + 1
    for term, count in capitalized_counts.items():
        acronym = term.isupper() or any(char.isdigit() for char in term)
        if count >= 2 or (acronym and len(term) >= 2):
            add(term, 1.2, count)

    cjk_counts: dict[str, int] = {}
    for run in _CJK_RUN_RE.findall(text_without_code):
        for size in (2, 3, 4):
            if len(run) < size:
                continue
            for index in range(len(run) - size + 1):
                gram = run[index : index + size]
                if gram in _CJK_STOP_TERMS:
                    continue
                cjk_counts[gram] = cjk_counts.get(gram, 0) + 1
    for gram, count in cjk_counts.items():
        if count >= 3:
            add(gram, 1.5, count)

    ranked = sorted(scores, key=lambda term: (-scores[term], order[term]))
    selected = _drop_redundant(ranked, cjk_counts, limit)
    return [GlossaryEntry(source=term, target=term) for term in selected]


def _build_glossary_user_content(text: str) -> str:
    clipped = text[:_MAX_DOC_CHARS_FOR_EXTRACTION]
    return f"文档内容：\n<<<DOC_BEGIN>>>\n{clipped}\n<<<DOC_END>>>"


def _parse_glossary_response(raw: str) -> list[GlossaryEntry]:
    if not isinstance(raw, str):
        return []
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    entries: list[GlossaryEntry] = []
    for line in lines:
        cells = _split_glossary_line(line)
        if not cells:
            continue
        source, target = cells[0], cells[1]
        source = _clean_term(source)
        target = _clean_term(target)
        if not source or not target:
            continue
        if source.casefold() in {"source", "原文", "术语", "term"}:
            continue
        entries.append(GlossaryEntry(source=source, target=target))
    return entries


def _split_glossary_line(line: str) -> tuple[str, str] | None:
    value = line.strip()
    if not value or value.startswith("```"):
        return None
    value = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", value).strip()
    if not value:
        return None

    if "|" in value:
        cells = [cell.strip() for cell in value.strip("|").split("|")]
        cells = [cell for cell in cells if cell]
        if len(cells) >= 2:
            return cells[0], cells[1]
        return None
    if "\t" in value:
        cells = [cell.strip() for cell in value.split("\t") if cell.strip()]
        if len(cells) >= 2:
            return cells[0], cells[1]
    for separator in (" => ", " -> ", "=>", "->", " — ", " – ", " → ", "→"):
        if separator in value:
            left, right = value.split(separator, 1)
            if left.strip() and right.strip():
                return left, right
    if ":" in value or "：" in value:
        left, right = re.split(r"[:：]", value, maxsplit=1)
        left_clean = left.strip()
        if left_clean and right.strip() and not re.match(
            r"^[a-z][a-z0-9+.-]*://", left_clean.lower()
        ):
            return left, right
    return None


def _clean_term(value: str) -> str:
    term = (value or "").strip()
    term = term.strip(" \t\r\n\"'“”‘’`*")
    term = re.sub(r"^[（(\[【]+|[）)\]】]+$", "", term).strip()
    if len(term) < 2 or len(term) > 80:
        return ""
    if term.casefold() in _STOPWORDS:
        return ""
    return term


def _is_stop_phrase(term: str) -> bool:
    words = [word.casefold() for word in re.split(r"[\s-]+", term) if word]
    return bool(words) and all(word in _STOPWORDS for word in words)


def _filter_present(entries: list[GlossaryEntry], text: str) -> list[GlossaryEntry]:
    folded = text.casefold()
    result: list[GlossaryEntry] = []
    for entry in entries:
        if entry.source.casefold() in folded:
            result.append(entry)
    return result


def _target_present(target: str, translated: str) -> bool:
    if not target:
        return True
    if _CJK_RE.search(target):
        return target in translated
    return target.casefold() in (translated or "").casefold()


def _drop_redundant(ranked: list[str], cjk_counts: dict[str, int], limit: int) -> list[str]:
    selected: list[str] = []
    for term in ranked:
        if len(selected) >= limit:
            break
        redundant = False
        for kept in selected:
            if term != kept and term in kept:
                term_count = cjk_counts.get(term, 0)
                kept_count = cjk_counts.get(kept, 0)
                if term_count and term_count <= kept_count:
                    redundant = True
                    break
        if not redundant:
            selected.append(term)
    return selected
