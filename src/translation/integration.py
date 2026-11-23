"""集成适配：把既有「translate(content, language, prompt_template)」形态的客户端
包装成本引擎要求的 ai_call(system_prompt, user_content) 形态。

旧客户端通常会对 prompt_template 执行 str.format(target_language=...)，
而翻译提示词中包含 Markdown/占位符示例的花括号；适配层会先转义花括号，
使二次格式化后得到原始提示词，避免 KeyError。
"""
from __future__ import annotations

from typing import Callable, Protocol


class TranslateStyleClient(Protocol):
    def translate(
        self,
        content: str,
        target_language: str,
        prompt_template: str = ...,
        log_callback: Callable[[str], None] | None = ...,
    ) -> str: ...


def escape_format_braces(text: str) -> str:
    """把文本中的花括号转义为可安全二次 str.format 的形式。"""
    return (text or "").replace("{", "{{").replace("}", "}}")


def make_legacy_ai_call(
    client: TranslateStyleClient,
    target_language: str,
) -> Callable[[str, str], str]:
    """把 translate 形态的客户端适配为 ai_call(system_prompt, user_content)。

    系统提示词作为 prompt_template 传入，用户内容作为待翻译文本传入；
    target_language 由集成方在构造时提供。
    """
    language = (target_language or "").strip()

    def ai_call(system_prompt: str, user_content: str) -> str:
        return client.translate(
            user_content,
            language,
            prompt_template=escape_format_braces(system_prompt),
        )

    return ai_call
