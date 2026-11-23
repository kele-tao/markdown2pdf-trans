"""多语言与换行测试：中文、俄语、阿拉伯语文本与 CJK 长段落切分。"""
from __future__ import annotations

import pytest

from src.translation import TranslationOptions, clear_translation_cache, translate_markdown
from src.translation.markdown_parser import BlockKind, parse_markdown

from tests.test_translation_support import build_translator


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_translation_cache()
    yield
    clear_translation_cache()


def test_chinese_document_to_english():
    doc = (
        "# 安装指南\n\n"
        "安装之前请阅读说明。\n\n"
        "- 第一步：准备服务器\n"
        "- 第二步：安装软件包\n\n"
        "> 注意：请先备份配置。\n"
    )
    mapping = {
        "安装指南": "Installation Guide",
        "安装之前请阅读说明。": "Read the instructions before installation.",
        "第一步：准备服务器": "First step: prepare the server",
        "第二步：安装软件包": "Second step: install the package",
        "注意：请先备份配置。": "Note: back up the configuration first.",
    }
    result = translate_markdown(
        doc, TranslationOptions("English"), ai_call=build_translator(mapping)
    )

    assert result.failed_blocks == []
    assert "# Installation Guide" in result.markdown
    assert "Read the instructions before installation." in result.markdown
    assert "First step: prepare the server" in result.markdown
    assert "> Note: back up the configuration first." in result.markdown
    assert [block.kind for block in parse_markdown(result.markdown).blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.LIST,
        BlockKind.BLOCKQUOTE,
    ]


def test_russian_document_to_english():
    doc = (
        "# Установка\n\n"
        "Прочитайте инструкцию перед началом работы.\n\n"
        "- Первый шаг\n"
        "- Второй шаг\n"
    )
    mapping = {
        "Установка": "Installation",
        "Прочитайте инструкцию перед началом работы.": "Read the instructions before you start.",
        "Первый шаг": "First step",
        "Второй шаг": "Second step",
    }
    result = translate_markdown(
        doc, TranslationOptions("English"), ai_call=build_translator(mapping)
    )

    assert result.failed_blocks == []
    assert "# Installation" in result.markdown
    assert "Read the instructions before you start." in result.markdown
    assert "- First step" in result.markdown


def test_english_document_to_arabic():
    doc = (
        "# Installation\n\n"
        "Read this before you start.\n\n"
        "- First step\n"
        "- Second step\n"
    )
    mapping = {
        "Installation": "التثبيت",
        "Read this before you start.": "اقرأ هذا قبل أن تبدأ.",
        "First step": "الخطوة الأولى",
        "Second step": "الخطوة الثانية",
    }
    result = translate_markdown(
        doc, TranslationOptions("Arabic"), ai_call=build_translator(mapping)
    )

    assert result.failed_blocks == []
    assert "# التثبيت" in result.markdown
    assert "اقرأ هذا قبل أن تبدأ." in result.markdown
    assert "- الخطوة الأولى" in result.markdown


def test_arabic_document_to_english():
    doc = (
        "# دليل التثبيت\n\n"
        "اقرأ التعليمات قبل البدء.\n\n"
        "- الخطوة الأولى\n"
        "- الخطوة الثانية\n"
    )
    mapping = {
        "دليل التثبيت": "Installation Guide",
        "اقرأ التعليمات قبل البدء.": "Read the instructions before you start.",
        "الخطوة الأولى": "First step",
        "الخطوة الثانية": "Second step",
    }
    result = translate_markdown(
        doc, TranslationOptions("English"), ai_call=build_translator(mapping)
    )

    assert result.failed_blocks == []
    assert "# Installation Guide" in result.markdown
    assert "Read the instructions before you start." in result.markdown


def test_chinese_long_paragraph_is_split_and_joined_cleanly():
    sentences = "".join(f"第{index}句说明部署流程。" for index in range(1, 15))
    doc = "# 标题\n\n" + sentences
    mapping = {
        f"第{index}句说明部署流程。": f"Sentence {index:02d} explains the deployment pipeline."
        for index in range(1, 15)
    }
    calls: list[str] = []
    fake = build_translator(mapping, calls=calls)

    result = translate_markdown(
        doc,
        TranslationOptions("English", max_chars_per_chunk=40, concurrency=1),
        ai_call=fake,
    )

    paragraph_calls = [payload for payload in calls if "句说明" in payload]
    assert len(paragraph_calls) >= 3
    assert all(len(payload) <= 40 for payload in paragraph_calls)
    assert result.failed_blocks == []

    parsed = parse_markdown(result.markdown).blocks
    assert [block.kind for block in parsed] == [BlockKind.HEADING, BlockKind.PARAGRAPH]
    paragraph = parsed[1].text
    assert "\n" not in paragraph
    positions = [paragraph.index(f"Sentence {index:02d}") for index in range(1, 15)]
    assert positions == sorted(positions)
