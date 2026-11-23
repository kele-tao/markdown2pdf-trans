"""翻译引擎的公共数据模型与选项。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class GlossaryEntry:
    """一条术语对照：原文术语与目标语言固定译法。"""

    source: str
    target: str


@dataclass
class TranslationOptions:
    """一次翻译任务的配置。

    - target_language: 目标语言名称（如 English、中文），必填。
    - glossary: 任务级术语表，命中术语会在提示词中强制指定译法。
    - tone: professional / academic / casual，决定文体指令。
    - max_chars_per_chunk: 单个 AI 请求允许的最大字符数；仅超长段落会按句子边界二次切分。
    - concurrency: 并发翻译的块数（按块并行，结果按原文顺序拼回）。
    - cancel_event: 置位后尽快停止派发与重试，未完成块保留原文。
    - on_progress: 进度回调，接收中文进度描述；回调异常不影响翻译。
    - max_retries: 结构校验失败或调用异常时的额外重试次数（不含首次尝试）。
    - cache_namespace: 缓存命名空间，集成方可传入模型名等标识，避免换模型后误命中旧缓存。
    """

    target_language: str
    glossary: list[GlossaryEntry] = field(default_factory=list)
    tone: str = "professional"
    max_chars_per_chunk: int = 6000
    concurrency: int = 4
    cancel_event: threading.Event | None = None
    on_progress: Callable[[str], None] | None = None
    max_retries: int = 2
    cache_namespace: str = ""


@dataclass
class TranslationResult:
    """翻译结果。

    - markdown: 翻译后的完整 Markdown；失败块保留原文，顺序与原文一致。
    - warnings: 中文告警，含块位置与原因（结构校验失败、调用失败、取消、术语未按表译出等）。
    - blocks_total: 需要翻译的块总数（不含代码块、分隔线、front matter 等不可翻译块）。
    - blocks_translated: 成功替换为译文的块数（含缓存命中）。
    - failed_blocks: 未成功翻译的块序号（1 起，按文档顺序），含因取消而未处理的块。
    - cancelled: 是否因 cancel_event 提前结束。
    """

    markdown: str
    warnings: list[str]
    blocks_total: int
    blocks_translated: int
    failed_blocks: list[int]
    cancelled: bool = False
