"""翻译引擎主流程：切分、上下文、并发、校验重试、缓存、取消与结果拼回。"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from .cache import get_default_cache, make_cache_key
from .glossary import find_matching_entries, normalize_entries, verify_glossary_usage
from .markdown_parser import (
    BLOCK_LABELS,
    TRANSLATABLE_KINDS,
    Block,
    ParsedDocument,
    parse_markdown,
)
from .models import GlossaryEntry, TranslationOptions, TranslationResult
from .prompts import (
    PROMPT_VERSION,
    build_retry_note,
    build_system_prompt,
    build_user_content,
    resolve_tone,
)
from .splitter import join_segments, split_block_segments
from .validation import validate_translation

logger = logging.getLogger(__name__)

_META_LINE_RE = re.compile(
    r"^(?:here is|here's|here are|以下是|译文如下|翻译如下|translation|translated text).*[:：]\s*$",
    re.IGNORECASE,
)
_OUTER_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)$")


@dataclass
class _BlockOutcome:
    text: Optional[str]
    warnings: list[str]
    cancelled: bool = False


class _TranslationRun:
    def __init__(
        self,
        document: ParsedDocument,
        blocks: list[Block],
        options: TranslationOptions,
        ai_call: Callable[[str, str], str],
    ) -> None:
        self.document = document
        self.blocks = blocks
        self.options = options
        self.ai_call = ai_call
        self.language = (options.target_language or "").strip()
        self.tone = resolve_tone(options.tone)
        self.glossary: list[GlossaryEntry] = normalize_entries(options.glossary)
        self.max_chars = max(1, int(options.max_chars_per_chunk or 1))
        self.max_retries = max(0, int(options.max_retries))
        self.cancel_event = options.cancel_event
        self.cache = get_default_cache()
        self.namespace = options.cache_namespace or ""
        self.total = len(blocks)
        self.workers = max(1, min(int(options.concurrency or 1), self.total))
        self._snippets = self._build_snippets()

    def execute(self) -> TranslationResult:
        if self._is_cancelled():
            warnings = [f"已取消：全部 {self.total} 块保留原文。"]
            return TranslationResult(
                markdown=self._assemble({}),
                warnings=warnings,
                blocks_total=self.total,
                blocks_translated=0,
                failed_blocks=[block.number for block in self.blocks],
                cancelled=True,
            )

        self._notify(f"开始翻译：共 {self.total} 块，目标语言 {self.language}")
        outcomes: dict[int, _BlockOutcome] = {}
        if self.workers == 1:
            cancelled = self._run_sequential(outcomes)
        else:
            cancelled = self._run_concurrent(outcomes)
        return self._finalize(outcomes, cancelled)

    def _run_sequential(self, outcomes: dict[int, _BlockOutcome]) -> bool:
        done = 0
        cancelled = False
        for block in self.blocks:
            if self._is_cancelled():
                cancelled = True
                break
            outcome = self._translate_block(block)
            outcomes[block.number] = outcome
            if outcome.cancelled:
                cancelled = True
                break
            if outcome.text is not None:
                done += 1
                self._notify(self._progress(done, block))
        return cancelled

    def _run_concurrent(self, outcomes: dict[int, _BlockOutcome]) -> bool:
        done = 0
        cancelled = False
        pool = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="m2p-translation"
        )
        future_map = {pool.submit(self._translate_block, block): block for block in self.blocks}
        try:
            for future in as_completed(future_map):
                block = future_map[future]
                try:
                    outcome = future.result()
                except Exception as exc:  # 单块内部错误不得拖垮整篇
                    logger.exception("块翻译出现未捕获异常")
                    outcome = _BlockOutcome(
                        text=None,
                        warnings=[
                            self._failure_warning(block, 1, 1, [], _format_error(exc))
                        ],
                    )
                outcomes[block.number] = outcome
                if outcome.cancelled:
                    cancelled = True
                if outcome.text is not None:
                    done += 1
                    self._notify(self._progress(done, block))
                if self._is_cancelled():
                    cancelled = True
                    break
        finally:
            if cancelled or self._is_cancelled():
                for future in future_map:
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
            else:
                pool.shutdown(wait=True)
        return cancelled

    def _translate_block(self, block: Block) -> _BlockOutcome:
        segments = split_block_segments(block, self.max_chars)
        matched_all = find_matching_entries(block.text, self.glossary)
        translated_parts: list[str] = []

        for index, segment in enumerate(segments, start=1):
            if self._is_cancelled():
                return _BlockOutcome(text=None, warnings=[], cancelled=True)
            text, issues, error = self._translate_segment(block, segment, index, len(segments))
            if text is None:
                if self._is_cancelled():
                    return _BlockOutcome(text=None, warnings=[], cancelled=True)
                warning = self._failure_warning(
                    block, index, len(segments), issues, error
                )
                logger.warning("%s", warning)
                return _BlockOutcome(text=None, warnings=[warning], cancelled=False)
            translated_parts.append(text)

        joined = join_segments(translated_parts, self.language)
        label = self._block_label(block)
        term_warnings = [
            f"{label}：{message}，请人工复核。"
            for message in verify_glossary_usage(joined, matched_all)
        ]
        return _BlockOutcome(text=joined, warnings=term_warnings, cancelled=False)

    def _translate_segment(
        self,
        block: Block,
        text: str,
        part_index: int,
        part_total: int,
    ) -> tuple[Optional[str], list[str], Optional[str]]:
        matched = find_matching_entries(text, self.glossary)
        key = make_cache_key(
            text,
            self.language,
            matched,
            self.tone,
            PROMPT_VERSION,
            self.namespace,
        )
        cached = self.cache.get(key)
        if cached is not None:
            logger.debug("翻译缓存命中：第 %s 块", block.number)
            return cached, [], None

        system_prompt = build_system_prompt(self.language, self.tone, matched)
        previous, following = self._snippets.get(block.number, ("", ""))
        base_user = build_user_content(
            title=self.document.title,
            summary=self.document.summary,
            path=block.path,
            previous=previous,
            following=following,
            kind=block.kind,
            part_index=part_index,
            part_total=part_total,
            text=text,
        )
        user_content = base_user
        issues: list[str] = []
        last_error: Optional[str] = None

        for attempt in range(self.max_retries + 1):
            if self._is_cancelled():
                return None, issues, "已取消"
            try:
                raw = self.ai_call(system_prompt, user_content)
            except Exception as exc:
                last_error = _format_error(exc)
                issues = [last_error]
                logger.warning("第 %s 块 AI 调用失败（第 %s 次）：%s", block.number, attempt + 1, last_error)
                if attempt < self.max_retries:
                    self._backoff(attempt)
                continue

            cleaned = clean_model_output(raw)
            found = validate_translation(text, cleaned, block.kind, block.level)
            if not found:
                self.cache.put(key, cleaned)
                return cleaned, [], None

            issues = found
            last_error = None
            logger.info(
                "第 %s 块第 %s 次译文未通过结构校验：%s",
                block.number,
                attempt + 1,
                "；".join(found),
            )
            user_content = base_user + build_retry_note(found)
            if attempt < self.max_retries:
                self._backoff(attempt)

        return None, issues, last_error

    def _finalize(self, outcomes: dict[int, _BlockOutcome], cancelled: bool) -> TranslationResult:
        warnings: list[str] = []
        failed_blocks: list[int] = []
        translated = 0
        missing: list[int] = []

        for block in self.blocks:
            outcome = outcomes.get(block.number)
            if outcome is None:
                failed_blocks.append(block.number)
                missing.append(block.number)
                continue
            warnings.extend(outcome.warnings)
            if outcome.text is None:
                failed_blocks.append(block.number)
            else:
                translated += 1

        if cancelled:
            if missing:
                warnings.append(
                    f"已取消：第 {missing[0]} 块至第 {missing[-1]} 块（共 {len(missing)} 块）保留原文。"
                )
            else:
                warnings.append("已取消：已完成的译文已保留，未完成的块保留原文。")

        self._notify(
            f"翻译结束：成功 {translated}/{self.total} 块"
            + (f"，失败 {len(failed_blocks)} 块" if failed_blocks else "")
            + ("（已取消）" if cancelled else "")
        )

        return TranslationResult(
            markdown=self._assemble(outcomes),
            warnings=warnings,
            blocks_total=self.total,
            blocks_translated=translated,
            failed_blocks=failed_blocks,
            cancelled=cancelled,
        )

    def _assemble(self, outcomes: dict[int, _BlockOutcome]) -> str:
        pieces: list[str] = []
        for block in self.document.blocks:
            pieces.append(block.gap)
            if block.kind in TRANSLATABLE_KINDS:
                outcome = outcomes.get(block.number)
                if outcome is not None and outcome.text is not None:
                    pieces.append(outcome.text)
                else:
                    pieces.append(block.text)
            else:
                pieces.append(block.text)
        pieces.append(self.document.trailer)
        return "".join(pieces)

    def _build_snippets(self) -> dict[int, tuple[str, str]]:
        snippets: dict[int, tuple[str, str]] = {}
        for index, block in enumerate(self.blocks):
            previous = _snippet(self.blocks[index - 1].text) if index > 0 else ""
            following = (
                _snippet(self.blocks[index + 1].text) if index + 1 < len(self.blocks) else ""
            )
            snippets[block.number] = (previous, following)
        return snippets

    def _block_label(self, block: Block) -> str:
        kind_label = BLOCK_LABELS.get(block.kind, "文本")
        if block.path:
            return f"第 {block.number} 块（{kind_label}，章节「{' > '.join(block.path)}」）"
        return f"第 {block.number} 块（{kind_label}）"

    def _failure_warning(
        self,
        block: Block,
        part_index: int,
        part_total: int,
        issues: list[str],
        error: Optional[str],
    ) -> str:
        label = self._block_label(block)
        if part_total > 1:
            label += f" 第 {part_index}/{part_total} 段"
        if error:
            reason = error
        elif issues:
            reason = "格式校验未通过：" + "；".join(issues[:3])
        else:
            reason = "未知原因"
        retry_hint = f"，已重试 {self.max_retries} 次" if self.max_retries else ""
        return f"{label}：{reason}{retry_hint}，已保留原文。"

    def _progress(self, done: int, block: Block) -> str:
        section = " > ".join(block.path) if block.path else "文档开头"
        return f"已完成 {done}/{self.total} 块：{section}"

    def _notify(self, message: str) -> None:
        callback = self.options.on_progress
        if callback is None:
            return
        try:
            callback(message)
        except Exception:
            logger.warning("on_progress 回调异常，已忽略", exc_info=True)

    def _is_cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _backoff(self, attempt: int) -> None:
        delay = min(0.1 * (2 ** attempt), 2.0)
        if self.cancel_event is not None:
            self.cancel_event.wait(delay)
        else:
            time.sleep(delay)


def translate_markdown(
    md: str,
    options: TranslationOptions,
    *,
    ai_call: Callable[[str, str], str] | None = None,
) -> TranslationResult:
    """翻译整篇 Markdown。

    ai_call(system_prompt, user_content) -> str 是 AI 调用注入点：生产环境由集成方
    包装现有客户端，测试注入假函数。本包不直接依赖任何 AI 客户端实现。
    """
    if not isinstance(md, str):
        raise TypeError("md 必须是字符串")
    if options is None:
        raise TypeError("options 不能为空")

    document = parse_markdown(md)
    blocks = document.translatable_blocks()
    if not md.strip() or not blocks:
        return TranslationResult(
            markdown=md,
            warnings=[],
            blocks_total=0,
            blocks_translated=0,
            failed_blocks=[],
        )
    if options.cancel_event is not None and options.cancel_event.is_set():
        return TranslationResult(
            markdown=md,
            warnings=[f"已取消：全部 {len(blocks)} 块保留原文。"],
            blocks_total=len(blocks),
            blocks_translated=0,
            failed_blocks=[block.number for block in blocks],
            cancelled=True,
        )
    if not (options.target_language or "").strip():
        raise ValueError("target_language 不能为空")
    if ai_call is None:
        raise ValueError(
            "translate_markdown 需要注入 ai_call(system_prompt, user_content) 才能翻译"
        )

    return _TranslationRun(document, blocks, options, ai_call).execute()


def clean_model_output(raw: object) -> str:
    """清理模型输出：去空白、去前言、拆掉包裹整段的外层代码围栏。"""
    if not isinstance(raw, str):
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = text.lstrip("\ufeff").strip()
    lines = text.split("\n")
    while len(lines) > 1 and _META_LINE_RE.match(lines[0].strip()):
        lines.pop(0)
    text = "\n".join(lines).strip()
    return _unwrap_outer_fence(text).strip()


def _unwrap_outer_fence(text: str) -> str:
    lines = text.split("\n")
    if len(lines) < 3:
        return text
    opening = _OUTER_FENCE_OPEN_RE.match(lines[0])
    if not opening:
        return text
    fence = opening.group(1)
    char = fence[0]
    length = len(fence)

    last_index = len(lines) - 1
    while last_index > 0 and not lines[last_index].strip():
        last_index -= 1
    closing = re.match(
        r"^ {0,3}(" + re.escape(char) + "{" + str(length) + r",})[ \t]*$",
        lines[last_index],
    )
    if not closing:
        return text
    inner = lines[1:last_index]
    if not any(line.strip() for line in inner):
        return text
    return "\n".join(inner)


def _snippet(text: str, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    return value if len(value) <= limit else value[:limit] + "…"


def _format_error(exc: BaseException) -> str:
    detail = str(exc).strip()
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return f"AI 调用失败（{type(exc).__name__}：{detail}）" if detail else f"AI 调用失败（{type(exc).__name__}）"
