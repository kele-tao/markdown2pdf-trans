"""任务级配置解析：把前端 JSON 规整成内部结构，并做白名单校验。"""
from __future__ import annotations

from typing import Any

from ..config import (
    BILINGUAL_LAYOUTS,
    DEFAULT_THEME,
    DOCUMENT_DEFAULTS,
    EXPORT_MODE_LABELS,
    LANGUAGES,
    STYLE_WHITELIST,
    resolve_theme,
)

_LANGUAGE_CODES = {code.casefold(): code for code, _label in LANGUAGES}
_EXPORT_MODES = set(EXPORT_MODE_LABELS)
_LAYOUTS = {key for key, _label in BILINGUAL_LAYOUTS}
_TONES = {"professional", "academic", "casual"}


class InvalidOptions(ValueError):
    """配置非法。message 直接可展示给用户。"""


def normalize_start_options(payload: dict[str, Any] | None) -> dict[str, Any]:
    """校验并补全启动参数。未知字段忽略，非法值抛 InvalidOptions。"""
    data = dict(payload or {})

    raw_language = str(data.get("target_language") or "English").strip()
    language = _LANGUAGE_CODES.get(raw_language.casefold())
    if language is None:
        raise InvalidOptions(f"不支持的目标语言：{raw_language}")

    export_mode = str(data.get("export_mode") or "translated").strip().lower()
    if export_mode not in _EXPORT_MODES:
        raise InvalidOptions(f"不支持的导出模式：{export_mode}")

    layout = str(data.get("bilingual_layout") or "side_by_side").strip().lower()
    if layout not in _LAYOUTS:
        raise InvalidOptions(f"不支持的双语版式：{layout}")

    tone = str(data.get("tone") or "professional").strip().lower()
    if tone not in _TONES:
        tone = "professional"

    theme_key = str(data.get("theme") or DEFAULT_THEME).strip().lower()
    theme = resolve_theme(theme_key)

    style_options: dict[str, Any] = {}
    incoming_style = data.get("style_options") or {}
    if isinstance(incoming_style, dict):
        for key in STYLE_WHITELIST:
            value = incoming_style.get(key)
            if value is None or value == "":
                continue
            style_options[key] = str(value).strip()
    style_options["theme"] = theme["key"]

    document = dict(DOCUMENT_DEFAULTS)
    incoming_doc = data.get("document") or {}
    if isinstance(incoming_doc, dict):
        for key in DOCUMENT_DEFAULTS:
            if key not in incoming_doc:
                continue
            value = incoming_doc[key]
            if isinstance(DOCUMENT_DEFAULTS[key], bool):
                document[key] = bool(value)
            else:
                document[key] = str(value or "").strip()

    glossary = []
    for entry in data.get("glossary") or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        target = str(entry.get("target") or "").strip()
        if source and target:
            glossary.append({"source": source, "target": target})

    return {
        "target_language": language,
        "export_mode": export_mode,
        "theme": theme["key"],
        "tone": tone,
        "bilingual_layout": layout,
        "glossary": glossary[:500],
        "style_options": style_options,
        "document": document,
    }
