"""集成适配测试：旧 translate 形态客户端的提示词花括号转义。"""
from __future__ import annotations

import pytest

from src.translation import (
    GlossaryEntry,
    TranslationOptions,
    clear_translation_cache,
    make_legacy_ai_call,
    translate_markdown,
)

from tests.test_translation_support import extract_payload


class LegacyClient:
    """模拟既有 AIClient：会对 prompt_template 执行 format(target_language=...)。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.contents: list[str] = []
        self.languages: list[str] = []

    def translate(
        self,
        content: str,
        target_language: str,
        prompt_template: str = "",
        log_callback=None,
    ) -> str:
        prompt = prompt_template.format(target_language=target_language)
        self.prompts.append(prompt)
        self.contents.append(content)
        self.languages.append(target_language)
        return extract_payload(content)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_translation_cache()
    yield
    clear_translation_cache()


def test_legacy_adapter_keeps_prompt_braces_and_glossary():
    client = LegacyClient()
    doc = "Hello world.\n"
    glossary = [GlossaryEntry("Hello", "你好")]

    result = translate_markdown(
        doc,
        TranslationOptions("Chinese", glossary=glossary),
        ai_call=make_legacy_ai_call(client, "Chinese"),
    )

    assert result.failed_blocks == []
    assert client.languages == ["Chinese"]
    assert "{{name}}" in client.prompts[0]
    assert "- Hello → 你好" in client.prompts[0]
    assert "Chinese" in client.prompts[0]
    assert client.contents[0].startswith("【文档信息】")


def test_legacy_adapter_tolerates_braces_in_glossary_target():
    client = LegacyClient()
    doc = "Hello world.\n"
    glossary = [GlossaryEntry("Hello", "{你好}")]

    result = translate_markdown(
        doc,
        TranslationOptions("Chinese", glossary=glossary),
        ai_call=make_legacy_ai_call(client, "Chinese"),
    )

    assert result.failed_blocks == []
    assert "{你好}" in client.prompts[0]
