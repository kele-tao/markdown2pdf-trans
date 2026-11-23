"""术语表与文件名翻译测试：命中边界、提示词注入、术语核对、候选提取、降级。"""
from __future__ import annotations

import pytest

from src.translation import (
    GlossaryEntry,
    TranslationOptions,
    clear_translation_cache,
    suggest_glossary,
    translate_filename,
    translate_markdown,
)
from src.translation.glossary import find_matching_entries, heuristic_terms

from tests.test_translation_support import build_translator, extract_payload


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_translation_cache()
    yield
    clear_translation_cache()


def test_glossary_only_injected_for_matched_blocks():
    doc = (
        "The deployment process is documented.\n\n"
        "Predeployment checks are separate.\n\n"
        "DEPLOYMENT checklist.\n"
    )
    glossary = [GlossaryEntry("deployment", "部署")]
    prompts: dict[str, str] = {}

    def fake(system: str, user: str) -> str:
        payload = extract_payload(user)
        prompts[payload] = system
        if payload.startswith("The deployment"):
            return "The 部署 process is documented."
        if payload.startswith("DEPLOYMENT"):
            return "部署 checklist."
        return payload

    result = translate_markdown(
        doc, TranslationOptions("Chinese", glossary=glossary, concurrency=1), ai_call=fake
    )

    assert result.failed_blocks == []
    assert result.warnings == []
    assert "必须使用指定译法" in prompts["The deployment process is documented."]
    assert "- deployment → 部署" in prompts["The deployment process is documented."]
    assert "必须使用指定译法" not in prompts["Predeployment checks are separate."]
    assert "- deployment → 部署" in prompts["DEPLOYMENT checklist."]


def test_term_match_is_case_insensitive_and_word_bounded():
    entries = [GlossaryEntry("deployment", "部署")]

    assert [entry.source for entry in find_matching_entries("Deployment is here.", entries)] == ["deployment"]
    assert find_matching_entries("DEPLOYMENT", entries)
    assert find_matching_entries("Predeployment", entries) == []
    assert find_matching_entries("deployment_pipeline", entries) == []
    assert find_matching_entries("The deployment.", entries)


def test_missing_glossary_target_raises_block_warning():
    doc = "The deployment process is documented.\n"
    glossary = [GlossaryEntry("deployment", "部署")]
    fake = build_translator({})

    result = translate_markdown(
        doc, TranslationOptions("Chinese", glossary=glossary), ai_call=fake
    )

    assert result.failed_blocks == []
    assert any("术语「deployment」未按术语表译为「部署」" in warning for warning in result.warnings)


def test_suggest_glossary_parses_ai_response_and_filters_unknown_terms():
    doc = "Deployment guide. Deployment steps. API Gateway is required."
    raw = "```\nDeployment\t部署\nAPI Gateway => API 网关\nMissing Term → 缺失\n```"

    entries = suggest_glossary(doc, "Chinese", ai_call=lambda system, user: raw, max_terms=10)

    assert [(entry.source, entry.target) for entry in entries] == [
        ("Deployment", "部署"),
        ("API Gateway", "API 网关"),
    ]

    limited = suggest_glossary(doc, "Chinese", ai_call=lambda system, user: raw, max_terms=1)
    assert len(limited) == 1
    assert limited[0].source == "Deployment"


def test_suggest_glossary_parses_markdown_table_and_arrow_formats():
    doc = "Deployment guide. API Gateway is required."
    table = "| 原文 | 译文 |\n| --- | --- |\n| Deployment | 部署 |\n"
    arrow = "Deployment → 部署\nAPI Gateway → API 网关\n"

    table_entries = suggest_glossary(doc, "Chinese", ai_call=lambda system, user: table)
    assert [(entry.source, entry.target) for entry in table_entries] == [
        ("Deployment", "部署")
    ]

    arrow_entries = suggest_glossary(doc, "Chinese", ai_call=lambda system, user: arrow)
    assert [(entry.source, entry.target) for entry in arrow_entries] == [
        ("Deployment", "部署"),
        ("API Gateway", "API 网关"),
    ]


def test_suggest_glossary_falls_back_when_ai_fails():
    doc = (
        "Deployment guide. Deployment steps. Deployment notes. "
        "Run `render_pdf` now. Run `render_pdf` again."
    )

    def boom(system: str, user: str) -> str:
        raise RuntimeError("模型不可用")

    entries = suggest_glossary(doc, "Chinese", ai_call=boom, max_terms=10)

    sources = [entry.source for entry in entries]
    assert "render_pdf" in sources
    assert any(source.lower() == "deployment" for source in sources)
    assert all(entry.source == entry.target for entry in entries)


def test_suggest_glossary_falls_back_when_ai_returns_empty():
    doc = "Deployment guide. Deployment steps. Deployment notes."
    entries = suggest_glossary(doc, "Chinese", ai_call=lambda system, user: "", max_terms=5)
    assert entries
    assert any(entry.source.lower() == "deployment" for entry in entries)

    offline = suggest_glossary(doc, "Chinese", max_terms=5)
    assert offline
    assert suggest_glossary("", "Chinese") == []
    assert suggest_glossary(doc, "Chinese", max_terms=0) == []


def test_heuristic_terms_supports_chinese_documents():
    doc = "部署流程说明。部署流程需要检查。部署流程完成后通知。"
    entries = heuristic_terms(doc, 5)
    assert entries
    assert any("部署" in entry.source for entry in entries)


def test_translate_filename_preserves_extension_and_sanitizes():
    def ai(system: str, user: str) -> str:
        return "installation_guide\n"

    assert translate_filename("用户手册.md", "English", ai_call=ai) == "installation_guide.md"
    assert translate_filename("dir/用户手册.md", "English", ai_call=ai) == "dir/installation_guide.md"

    invalid = translate_filename("doc.md", "English", ai_call=lambda system, user: 'bad/name:?*"<>|')
    assert invalid == "badname.md"

    with_extension = translate_filename(
        "doc.md", "English", ai_call=lambda system, user: "guide.md"
    )
    assert with_extension == "guide.md"

    other_extension = translate_filename(
        "doc.md", "English", ai_call=lambda system, user: "guide.txt"
    )
    assert other_extension == "guide.md"

    prefixed = translate_filename(
        "doc.md", "English", ai_call=lambda system, user: "Here is the translation: guide"
    )
    assert prefixed == "guide.md"


def test_translate_filename_never_breaks_on_failure():
    def boom(system: str, user: str) -> str:
        raise RuntimeError("down")

    assert translate_filename("a.md", "English", ai_call=boom) == "a.md"
    assert translate_filename("a.md", "English") == "a.md"
    assert translate_filename(".env", "English", ai_call=lambda system, user: "config") == ".env"
    assert translate_filename("report", "English", ai_call=lambda system, user: "annual_report") == "annual_report"
    assert translate_filename("", "English", ai_call=lambda system, user: "x") == ""
