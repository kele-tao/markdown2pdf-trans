"""Markdown 翻译引擎。

公共接口：GlossaryEntry / TranslationOptions / TranslationResult、
translate_markdown / suggest_glossary / translate_filename。
AI 调用通过 ai_call(system_prompt, user_content) 注入，本包不依赖任何具体客户端。
"""
from __future__ import annotations

from .cache import clear_translation_cache
from .engine import translate_markdown
from .filename import translate_filename
from .glossary import suggest_glossary
from .integration import make_legacy_ai_call
from .models import GlossaryEntry, TranslationOptions, TranslationResult
from .prompts import PROMPT_VERSION

__all__ = [
    "GlossaryEntry",
    "TranslationOptions",
    "TranslationResult",
    "translate_markdown",
    "suggest_glossary",
    "translate_filename",
    "make_legacy_ai_call",
    "clear_translation_cache",
    "PROMPT_VERSION",
]
