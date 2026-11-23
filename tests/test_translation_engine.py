"""翻译引擎核心行为测试：切分、顺序、校验重试、并发、取消、缓存、边界输入。"""
from __future__ import annotations

import threading
import time

import pytest

from src.translation import (
    GlossaryEntry,
    TranslationOptions,
    clear_translation_cache,
    translate_markdown,
)
from src.translation.markdown_parser import BlockKind, parse_markdown

from tests.test_translation_support import (
    build_translator,
    extract_payload,
    load_fixture,
    long_document_mapping,
    marker_delay,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_translation_cache()
    yield
    clear_translation_cache()


def _fake(mapping=None):
    return build_translator(mapping or {})


def _positions(text: str, needles: list[str]) -> list[int]:
    return [text.index(needle) for needle in needles]


# ---------------------------------------------------------------- 长文与顺序

def test_long_document_blocks_order_and_integrity():
    doc = load_fixture()
    calls: list[str] = []
    progress: list[str] = []
    fake = build_translator(
        long_document_mapping(),
        calls=calls,
        delay=marker_delay,
    )

    result = translate_markdown(
        doc,
        TranslationOptions(
            "Chinese",
            concurrency=4,
            max_chars_per_chunk=6000,
            on_progress=progress.append,
        ),
        ai_call=fake,
    )

    assert result.blocks_total == 19
    assert result.blocks_translated == 19
    assert result.failed_blocks == []
    assert result.warnings == []
    assert result.cancelled is False

    markers = [f"T{index:02d}" for index in list(range(1, 6)) + list(range(7, 17))]
    positions = _positions(result.markdown, markers)
    assert positions == sorted(positions)

    # 代码块未参与翻译，原文保留
    assert "ALPHA6" in result.markdown
    assert all("ALPHA6" not in payload for payload in calls)
    assert all("This code must never be translated" not in payload for payload in calls)
    assert all("author: Docs Team" not in payload for payload in calls)

    # front matter 与表格原样保留
    assert result.markdown.startswith("---\ntitle: Deployment Guide")
    assert "| Disk | 20 GB | 50 GB |" in result.markdown

    # 全局块类型与标题级别序列不变
    original_shape = [(b.kind, b.level) for b in parse_markdown(doc).blocks]
    translated_shape = [(b.kind, b.level) for b in parse_markdown(result.markdown).blocks]
    assert translated_shape == original_shape

    assert progress[0].startswith("开始翻译")
    assert progress[-1].startswith("翻译结束")


def test_combined_long_document_with_glossary_and_small_chunks():
    doc = load_fixture()
    glossary = [GlossaryEntry("deployment", "部署")]
    result = translate_markdown(
        doc,
        TranslationOptions(
            "Chinese",
            glossary=glossary,
            max_chars_per_chunk=200,
            concurrency=4,
        ),
        ai_call=build_translator(long_document_mapping()),
    )

    assert result.blocks_total == 19
    assert result.blocks_translated == 19
    assert result.failed_blocks == []
    assert "deploy --target ALPHA6 --verbose" in result.markdown
    assert "| Disk | 20 GB | 50 GB |" in result.markdown
    original_shape = [(b.kind, b.level) for b in parse_markdown(doc).blocks]
    translated_shape = [(b.kind, b.level) for b in parse_markdown(result.markdown).blocks]
    assert translated_shape == original_shape


def test_code_and_table_kept_whole():
    doc = load_fixture()
    calls: list[str] = []
    fake = build_translator({"Deployment": "部署"}, calls=calls)

    result = translate_markdown(
        doc,
        TranslationOptions("Chinese", concurrency=2, max_chars_per_chunk=120),
        ai_call=fake,
    )

    assert result.failed_blocks == []
    assert result.markdown.count("```") == 2
    assert "```bash" in result.markdown
    assert "deploy --target ALPHA6 --verbose" in result.markdown
    assert "ALPHA6" not in "".join(calls)

    table_calls = [payload for payload in calls if "| Component | Minimum | Recommended |" in payload]
    assert len(table_calls) == 1
    assert "| Disk | 20 GB | 50 GB |" in table_calls[0]


def test_long_paragraph_split_by_sentence_boundary():
    sentences = " ".join(
        f"Sentence {index} carries marker PG{index:02d}." for index in range(1, 13)
    )
    doc = "# Title\n\n" + sentences
    mapping = {f"PG{index:02d}": f"句{index:02d}" for index in range(1, 13)}
    calls: list[str] = []
    fake = build_translator(mapping, calls=calls)

    result = translate_markdown(
        doc,
        TranslationOptions("Chinese", max_chars_per_chunk=100, concurrency=1),
        ai_call=fake,
    )

    assert result.blocks_total == 2
    assert result.blocks_translated == 2
    assert result.failed_blocks == []

    paragraph_calls = [payload for payload in calls if "PG" in payload]
    assert len(paragraph_calls) >= 4
    assert all(len(payload) <= 100 for payload in paragraph_calls)

    parsed = parse_markdown(result.markdown).blocks
    assert [block.kind for block in parsed] == [BlockKind.HEADING, BlockKind.PARAGRAPH]
    paragraph = parsed[1].text
    assert "\n" not in paragraph

    positions = _positions(paragraph, [f"句{index:02d}" for index in range(1, 13)])
    assert positions == sorted(positions)


def test_concurrent_blocks_are_reassembled_in_source_order():
    doc = "\n\n".join(f"Text block {index} marker C{index:02d}." for index in range(1, 13))
    mapping = {f"C{index:02d}": f"R{index:02d}" for index in range(1, 13)}

    def delay(payload: str) -> float:
        for index in range(1, 13):
            if f"C{index:02d}" in payload:
                return 0.04 if index % 2 else 0.01
        return 0.0

    calls: list[str] = []
    fake = build_translator(mapping, calls=calls, delay=delay)
    result = translate_markdown(
        doc,
        TranslationOptions("Chinese", concurrency=4),
        ai_call=fake,
    )

    assert result.blocks_translated == 12
    assert len(calls) == 12
    positions = _positions(result.markdown, [f"R{index:02d}" for index in range(1, 13)])
    assert positions == sorted(positions)


# ---------------------------------------------------------------- 校验与重试

def test_structure_break_retries_then_keeps_original():
    doc = "# Heading\n\nP1MARK first paragraph.\n\nP2MARK broken paragraph.\n"
    state = {"p1": 0, "p2": 0}

    def fake(system: str, user: str) -> str:
        payload = extract_payload(user)
        if "P1MARK" in payload:
            state["p1"] += 1
            if state["p1"] == 1:
                return "## Wrong level\n\n" + payload
            return payload.replace("P1MARK", "P1DONE")
        if "P2MARK" in payload:
            state["p2"] += 1
            return "# Real heading\n"
        return payload

    result = translate_markdown(doc, TranslationOptions("English"), ai_call=fake)

    assert state["p1"] == 2
    assert state["p2"] == 3  # 首次 + 默认 2 次重试
    assert result.blocks_translated == 2
    assert result.failed_blocks == [3]
    assert "P1DONE" in result.markdown
    assert "P2MARK broken paragraph." in result.markdown
    assert any("第 3 块" in warning and "保留原文" in warning for warning in result.warnings)


def test_placeholder_loss_is_rejected():
    doc = "Hello {{user_name}}, your order is ready.\n"
    broken = lambda system, user: "Hello, your order is ready.\n"

    result = translate_markdown(
        doc, TranslationOptions("Chinese", max_retries=1), ai_call=broken
    )

    assert result.failed_blocks == [1]
    assert result.markdown == doc
    assert any("占位符" in warning for warning in result.warnings)


def test_tone_and_target_language_reach_prompt():
    doc = "Paragraph text.\n"
    prompts: list[str] = []
    for tone in ("professional", "academic", "casual"):
        translate_markdown(
            doc,
            TranslationOptions("French", tone=tone),
            ai_call=build_translator({}, prompts=prompts),
        )

    assert len(prompts) == 3
    assert len(set(prompts)) == 3
    assert all("French" in prompt for prompt in prompts)
    assert any("学术" in prompt for prompt in prompts)
    assert any("口语" in prompt for prompt in prompts)


def test_footnote_markers_are_preserved():
    doc = "A claim with a footnote[^1].\n\n[^1]: The source of the claim.\n"
    mapping = {
        "A claim with a footnote": "一个有脚注的论断",
        "The source of the claim.": "该论断的来源。",
    }
    result = translate_markdown(
        doc, TranslationOptions("Chinese"), ai_call=build_translator(mapping)
    )

    assert result.failed_blocks == []
    assert result.markdown.count("[^1]") == 2


def test_footnote_marker_loss_is_rejected():
    doc = "A claim with a footnote[^1].\n"
    fake = lambda system, user: "A claim with a footnote.\n"

    result = translate_markdown(
        doc, TranslationOptions("Chinese", max_retries=1), ai_call=fake
    )

    assert result.failed_blocks == [1]
    assert result.markdown == doc
    assert any("脚注" in warning for warning in result.warnings)


def test_table_column_loss_is_rejected():
    doc = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    fake = _fake()
    broken = lambda system, user: "| A |\n| --- |\n| 1 |\n"

    result = translate_markdown(
        doc,
        TranslationOptions("English", max_retries=1),
        ai_call=broken,
    )

    assert result.blocks_translated == 0
    assert result.failed_blocks == [1]
    assert result.markdown == doc
    assert any("列数" in warning for warning in result.warnings)


def test_inline_code_change_is_rejected():
    doc = "Use `pip install demo` to install.\n"
    fake = lambda system, user: "Use `pnpm add demo` to install.\n"

    result = translate_markdown(
        doc, TranslationOptions("English", max_retries=1), ai_call=fake
    )

    assert result.failed_blocks == [1]
    assert result.markdown == doc
    assert any("行内代码" in warning for warning in result.warnings)


def test_link_loss_is_rejected():
    doc = "Read [the guide](https://docs.example.com/a) first.\n"
    fake = lambda system, user: "Read the guide first.\n"

    result = translate_markdown(
        doc, TranslationOptions("English", max_retries=1), ai_call=fake
    )

    assert result.failed_blocks == [1]
    assert "https://docs.example.com/a" in result.markdown
    assert any("链接" in warning for warning in result.warnings)


def test_heading_level_change_is_rejected():
    doc = "## Section\n\nBody text.\n"

    def fake(system: str, user: str) -> str:
        payload = extract_payload(user)
        if payload.startswith("## "):
            return "### Section\n"
        return payload

    result = translate_markdown(
        doc, TranslationOptions("English", max_retries=1), ai_call=fake
    )

    assert result.failed_blocks == [1]
    assert "## Section" in result.markdown
    assert any("标题级别" in warning for warning in result.warnings)


def test_ai_exception_does_not_break_other_blocks():
    doc = "FIRSTMARK okay.\n\nSECONDMARK fails.\n"

    def fake(system: str, user: str) -> str:
        payload = extract_payload(user)
        if "SECONDMARK" in payload:
            raise RuntimeError("模型服务不可用")
        return payload.replace("FIRSTMARK", "FIRSTDONE")

    result = translate_markdown(
        doc, TranslationOptions("English", max_retries=0), ai_call=fake
    )

    assert result.blocks_translated == 1
    assert result.failed_blocks == [2]
    assert "FIRSTDONE" in result.markdown
    assert "SECONDMARK fails." in result.markdown
    assert any("模型服务不可用" in warning for warning in result.warnings)


def test_outer_fence_wrapper_is_unwrapped():
    doc = "A plain paragraph.\n"
    fake = lambda system, user: "```markdown\nA plain paragraph.\n```\n"

    result = translate_markdown(doc, TranslationOptions("English"), ai_call=fake)

    assert result.blocks_translated == 1
    assert result.failed_blocks == []
    assert result.markdown == "A plain paragraph.\n"


def test_chinese_preamble_is_stripped():
    doc = "A plain paragraph.\n"
    fake = lambda system, user: "以下是译文：\n\nA plain paragraph.\n"

    result = translate_markdown(doc, TranslationOptions("English"), ai_call=fake)

    assert result.failed_blocks == []
    assert result.markdown == "A plain paragraph.\n"


# ---------------------------------------------------------------- 取消

def test_cancel_stops_remaining_blocks():
    doc = "\n\n".join(f"Paragraph {index} marker P{index:02d}." for index in range(1, 7))
    event = threading.Event()
    calls: list[str] = []

    def fake(system: str, user: str) -> str:
        payload = extract_payload(user)
        calls.append(payload)
        if len(calls) >= 2:
            event.set()
        return payload

    result = translate_markdown(
        doc,
        TranslationOptions("English", concurrency=1, cancel_event=event),
        ai_call=fake,
    )

    assert result.cancelled is True
    assert len(calls) == 2
    assert result.blocks_total == 6
    assert result.blocks_translated == 2
    assert result.failed_blocks == [3, 4, 5, 6]
    assert "P03" in result.markdown
    assert any("取消" in warning for warning in result.warnings)


def test_cancel_in_concurrent_mode_returns_promptly():
    doc = "\n\n".join(f"Block {index} marker K{index:02d}." for index in range(1, 25))
    event = threading.Event()
    lock = threading.Lock()
    calls: list[str] = []

    def fake(system: str, user: str) -> str:
        payload = extract_payload(user)
        with lock:
            calls.append(payload)
            first_call = len(calls) == 1
        if first_call:
            event.set()
        time.sleep(0.02)
        return payload

    result = translate_markdown(
        doc,
        TranslationOptions("English", concurrency=4, cancel_event=event),
        ai_call=fake,
    )

    assert result.cancelled is True
    assert result.blocks_translated < result.blocks_total
    assert result.failed_blocks
    assert len(calls) <= 4  # 取消后不再派发新块
    assert any("取消" in warning for warning in result.warnings)


def test_cancel_before_start_calls_nothing():
    doc = "# Title\n\nParagraph text.\n"
    event = threading.Event()
    event.set()

    result = translate_markdown(doc, TranslationOptions("English", cancel_event=event))

    assert result.cancelled is True
    assert result.blocks_translated == 0
    assert result.markdown == doc
    assert result.failed_blocks == [1, 2]
    assert any("取消" in warning for warning in result.warnings)


# ---------------------------------------------------------------- 输入边界与缓存

def test_empty_and_whitespace_documents():
    calls: list[str] = []
    fake = build_translator({}, calls=calls)

    empty = translate_markdown("", TranslationOptions("English"), ai_call=fake)
    assert empty.markdown == ""
    assert empty.blocks_total == 0
    assert empty.blocks_translated == 0
    assert empty.failed_blocks == []

    whitespace = "   \n\n\t\n"
    blank = translate_markdown(whitespace, TranslationOptions("English"), ai_call=fake)
    assert blank.markdown == whitespace
    assert blank.blocks_total == 0
    assert calls == []


def test_document_without_translatable_blocks_is_untouched():
    doc = "```python\nprint('hi')\n```\n\n---\n\n$$\nx = 1\n$$\n"
    calls: list[str] = []
    result = translate_markdown(
        doc,
        TranslationOptions("English"),
        ai_call=build_translator({}, calls=calls),
    )

    assert calls == []
    assert result.blocks_total == 0
    assert result.markdown == doc


def test_missing_ai_call_and_language_are_rejected():
    with pytest.raises(ValueError):
        translate_markdown("Text.", TranslationOptions("English"))
    with pytest.raises(ValueError):
        translate_markdown("Text.", TranslationOptions("  "), ai_call=_fake())


def test_crlf_input_is_normalized_and_structure_preserved():
    doc = "# Title\r\n\r\nCRLF paragraph marker CR01.\r\n\r\n- item one\r\n- item two\r\n"
    fake = build_translator({"CRLF paragraph marker CR01.": "换行段落标记 CR01。"})

    result = translate_markdown(doc, TranslationOptions("Chinese"), ai_call=fake)

    assert "\r" not in result.markdown
    assert "换行段落标记 CR01。" in result.markdown
    assert [block.kind for block in parse_markdown(result.markdown).blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.LIST,
    ]


def test_glossary_is_part_of_cache_key():
    doc = "Deployment steps are here.\n"
    first_calls: list[str] = []
    first = translate_markdown(
        doc,
        TranslationOptions("Chinese", glossary=[GlossaryEntry("deployment", "部署")]),
        ai_call=build_translator({"Deployment": "部署"}, calls=first_calls),
    )
    assert first.failed_blocks == []
    assert len(first_calls) == 1

    second_calls: list[str] = []
    translate_markdown(
        doc,
        TranslationOptions("Chinese", glossary=[GlossaryEntry("deployment", "上线")]),
        ai_call=build_translator({"Deployment": "部署"}, calls=second_calls),
    )
    assert len(second_calls) == 1  # 术语不同，缓存 key 不同


def test_translation_cache_prevents_repeated_calls():
    doc = "# T\n\nSame paragraph marker SAME01.\n\nSame paragraph marker SAME01.\n"
    calls: list[str] = []
    fake = build_translator({"SAME01": "已译01"}, calls=calls)

    first = translate_markdown(doc, TranslationOptions("Chinese"), ai_call=fake)
    assert first.failed_blocks == []
    assert first.markdown.count("已译01") == 2
    # 标题 1 次 + 相同段落 1 次（第二次命中缓存）
    assert len(calls) == 2

    second_calls: list[str] = []
    second = translate_markdown(
        doc,
        TranslationOptions("Chinese"),
        ai_call=build_translator({}, calls=second_calls),
    )
    assert second_calls == []
    assert second.markdown == first.markdown

    tone_calls: list[str] = []
    translate_markdown(
        doc,
        TranslationOptions("Chinese", tone="academic"),
        ai_call=build_translator({}, calls=tone_calls),
    )
    assert len(tone_calls) == 2


def test_progress_callback_failure_is_ignored():
    doc = "# Title\n\nParagraph text.\n"

    def broken_progress(message: str) -> None:
        raise RuntimeError("进度回调坏了")

    result = translate_markdown(
        doc,
        TranslationOptions("English", on_progress=broken_progress),
        ai_call=_fake(),
    )

    assert result.blocks_translated == 2
    assert result.failed_blocks == []


def test_blank_line_separators_are_preserved():
    doc = "First paragraph.\n\n\n\nSecond paragraph.\n"
    result = translate_markdown(doc, TranslationOptions("English"), ai_call=_fake())
    assert result.markdown == doc


def test_leading_horizontal_rule_is_not_front_matter():
    doc = "---\n\nIntro paragraph HRMARK.\n\n---\n\nLater paragraph LATERMARK.\n"
    fake = build_translator({"HRMARK": "甲一", "LATERMARK": "乙二"})

    result = translate_markdown(doc, TranslationOptions("English"), ai_call=fake)

    assert result.blocks_total == 2
    assert "甲一" in result.markdown
    assert "乙二" in result.markdown
    assert result.markdown.startswith("---\n\n")


def test_options_glossary_default_is_not_shared():
    first = TranslationOptions("Chinese")
    second = TranslationOptions("English")
    first.glossary.append(GlossaryEntry("API", "接口"))
    assert second.glossary == []
