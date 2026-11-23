"""集中配置。

原则：
- 所有路径 / 限制 / 语言 / 主题集中在这里，其他模块只导入不硬编码。
- AI 密钥等可变配置在 `src.core.settings_store`（可运行时改），此处只保留静态项。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# 路径
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("M2P_DATA_DIR", BASE_DIR / "data")).resolve()
OUTPUT_DIR = Path(os.environ.get("M2P_OUTPUT_DIR", DATA_DIR / "output")).resolve()
TEMPLATE_DIR = BASE_DIR / "src" / "templates"
STATIC_DIR = BASE_DIR / "src" / "static"
FONT_DIR = Path(os.environ.get("M2P_FONT_DIR", BASE_DIR / "fonts")).resolve()

for _p in (DATA_DIR, OUTPUT_DIR):
    _p.mkdir(parents=True, exist_ok=True)

APP_VERSION = "4.0.0"

# -----------------------------------------------------------------------------
# 限制
# -----------------------------------------------------------------------------
MAX_UPLOAD_BYTES = int(os.environ.get("M2P_MAX_UPLOAD_MB", "100")) * 1024 * 1024
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // 1024 // 1024
MAX_IMAGE_BYTES = int(os.environ.get("M2P_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
TASK_TTL_SECONDS = int(os.environ.get("M2P_TASK_TTL_SECONDS", str(24 * 3600)))
TASK_CLEANUP_INTERVAL = int(os.environ.get("M2P_TASK_CLEANUP_INTERVAL", "1800"))
CACHE_MAX_ENTRIES = int(os.environ.get("M2P_CACHE_MAX_ENTRIES", "2000"))
CACHE_TTL_SECONDS = int(os.environ.get("M2P_CACHE_TTL_SECONDS", str(7 * 24 * 3600)))
MAX_CHARS_PER_CHUNK = int(os.environ.get("M2P_MAX_CHARS_PER_CHUNK", "6000"))

# -----------------------------------------------------------------------------
# 语言（新增语言只需在此追加；rtl 决定排版方向）
# -----------------------------------------------------------------------------
LANGUAGE_SPECS: list[dict[str, Any]] = [
    {"code": "English", "label": "英语 (English)", "native": "English", "rtl": False},
    {"code": "Chinese", "label": "中文 (Chinese)", "native": "中文", "rtl": False},
    {"code": "French", "label": "法语 (Français)", "native": "Français", "rtl": False},
    {"code": "Russian", "label": "俄语 (Русский)", "native": "Русский", "rtl": False},
    {"code": "Spanish", "label": "西班牙语 (Español)", "native": "Español", "rtl": False},
    {"code": "Arabic", "label": "阿拉伯语 (العربية)", "native": "العربية", "rtl": True},
]

LANGUAGES: list[tuple[str, str]] = [(item["code"], item["label"]) for item in LANGUAGE_SPECS]
_RTL_LANGUAGES = {item["code"].casefold() for item in LANGUAGE_SPECS if item["rtl"]}


def is_rtl_language(language: str) -> bool:
    return (language or "").strip().casefold() in _RTL_LANGUAGES


def language_spec(language: str) -> dict[str, Any]:
    for item in LANGUAGE_SPECS:
        if item["code"].casefold() == (language or "").strip().casefold():
            return item
    return {"code": language or "English", "label": language or "English", "native": language or "", "rtl": False}


# -----------------------------------------------------------------------------
# 导出模式
# -----------------------------------------------------------------------------
EXPORT_MODES: list[tuple[str, str]] = [
    ("translated", "仅译文（文件名同时翻译）"),
    ("original", "仅原文"),
    ("bilingual", "双语对照 + 单独译文"),
]

EXPORT_MODE_LABELS = dict(EXPORT_MODES)

BILINGUAL_LAYOUTS: list[tuple[str, str]] = [
    ("side_by_side", "并排对照（左右）"),
    ("stacked", "上下对照（原文在上）"),
]

TONE_OPTIONS: list[tuple[str, str]] = [
    ("professional", "专业（技术/商务）"),
    ("academic", "学术"),
    ("casual", "口语自然"),
]

PAGE_SIZES: list[str] = ["A4", "A5", "Letter", "Legal"]

# -----------------------------------------------------------------------------
# 字体策略：字体族链按「先本地内嵌字体、后系统回退」排列。
# 中文/阿拉伯语必须显式给出回退，避免 WeasyPrint 缺字显示方框。
# -----------------------------------------------------------------------------
FONT_STACKS: dict[str, dict[str, str]] = {
    "serif": {
        "family": '"M2P Serif", "Noto Serif", "Noto Serif SC", "Times New Roman", "Songti SC", "SimSun", "Noto Naskh Arabic", "Tahoma", serif',
        "label": "衬线（书籍/学术）",
    },
    "sans": {
        "family": '"M2P Sans", "Noto Sans", "Noto Sans SC", "Segoe UI", "Arial", "Microsoft YaHei", "Noto Naskh Arabic", "Tahoma", sans-serif',
        "label": "无衬线（文档/商务）",
    },
    "mono": {
        "family": '"JetBrains Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono", monospace',
        "label": "等宽（技术文档）",
    },
}

# WeasyPrint 渲染时注册的本地字体文件（存在才注册，缺失自动跳过）
FONT_FILES: list[str] = [
    "NotoSerifSC-VF.ttf",
    "NotoSansSC-VF.ttf",
    "NotoSansArabic-Regular.ttf",
    "NotoNaskhArabic-Regular.ttf",
]

# -----------------------------------------------------------------------------
# 版式主题（4 套成品 + 用户可覆盖各项）
# -----------------------------------------------------------------------------
THEME_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "name": "默认",
        "description": "通用技术文档：清晰的字号阶梯与克制的留白",
        "text_color": "#1f2328",
        "muted_color": "#5c6570",
        "heading_color": "#111418",
        "link_color": "#1f5fbf",
        "accent_color": "#1f5fbf",
        "quote_bg_color": "#f5f7fa",
        "quote_border_color": "#c9d3e0",
        "table_header_bg": "#eef2f7",
        "table_border_color": "#d5dce5",
        "code_bg": "#f6f8fa",
        "rule_color": "#dfe4ea",
        "font_stack": "serif",
        "font_family": '"M2P Serif", "Noto Serif", "Times New Roman", "Songti SC", "SimSun", "Noto Naskh Arabic", "Tahoma", serif',
        "font_size": "11pt",
        "line_height": "1.75",
        "text_align": "justify",
        "heading_weight": "700",
        "page_size": "A4",
        "page_orientation": "portrait",
        "page_margin": "2.2cm 2cm",
        "code_font_size": "0.86em",
        "cover_style": "plain",
        "header_rule": "true",
    },
    "academic": {
        "name": "学术",
        "description": "论文/研究报告：衬线正文、编号标题、素雅页眉",
        "text_color": "#17181a",
        "muted_color": "#5a5f66",
        "heading_color": "#0d0f12",
        "link_color": "#0b5394",
        "accent_color": "#0b5394",
        "quote_bg_color": "#f2f5f9",
        "quote_border_color": "#8aa8c6",
        "table_header_bg": "#e9eef4",
        "table_border_color": "#c6d1dd",
        "code_bg": "#f4f6f8",
        "rule_color": "#cfd6de",
        "font_stack": "serif",
        "font_family": '"M2P Serif", "Noto Serif", "Times New Roman", "Songti SC", "SimSun", "Noto Naskh Arabic", "Tahoma", serif',
        "font_size": "10.5pt",
        "line_height": "1.8",
        "text_align": "justify",
        "heading_weight": "700",
        "page_size": "A4",
        "page_orientation": "portrait",
        "page_margin": "2.4cm 2.2cm",
        "code_font_size": "0.82em",
        "cover_style": "rule",
        "header_rule": "true",
    },
    "business": {
        "name": "商务",
        "description": "对外交付：无衬线正文、品牌色标题、紧凑表格",
        "text_color": "#1a1d21",
        "muted_color": "#5d646d",
        "heading_color": "#0f3d6b",
        "link_color": "#0f62b5",
        "accent_color": "#0f62b5",
        "quote_bg_color": "#eef4fb",
        "quote_border_color": "#0f62b5",
        "table_header_bg": "#0f3d6b",
        "table_header_color": "#ffffff",
        "table_border_color": "#c8d3e0",
        "code_bg": "#f5f7f9",
        "rule_color": "#d8dee6",
        "font_stack": "sans",
        "font_family": '"M2P Sans", "Noto Sans", "Segoe UI", "Arial", "Microsoft YaHei", "Noto Naskh Arabic", "Tahoma", sans-serif',
        "font_size": "10.5pt",
        "line_height": "1.65",
        "text_align": "left",
        "heading_weight": "650",
        "page_size": "A4",
        "page_orientation": "portrait",
        "page_margin": "2cm 1.8cm",
        "code_font_size": "0.85em",
        "cover_style": "band",
        "header_rule": "true",
    },
    "modern": {
        "name": "现代",
        "description": "产品手册：大留白、强对比标题、圆角引用与代码块",
        "text_color": "#20242a",
        "muted_color": "#616a75",
        "heading_color": "#0b0d10",
        "link_color": "#0f766e",
        "accent_color": "#0f766e",
        "quote_bg_color": "#f0f7f6",
        "quote_border_color": "#0f766e",
        "table_header_bg": "#eef2f4",
        "table_border_color": "#ccd5da",
        "code_bg": "#f4f6f8",
        "rule_color": "#dde3e8",
        "font_stack": "sans",
        "font_family": '"M2P Sans", "Noto Sans", "Segoe UI", "Arial", "Microsoft YaHei", "Noto Naskh Arabic", "Tahoma", sans-serif',
        "font_size": "11pt",
        "line_height": "1.8",
        "text_align": "left",
        "heading_weight": "700",
        "page_size": "A4",
        "page_orientation": "portrait",
        "page_margin": "2.6cm 2.4cm",
        "code_font_size": "0.86em",
        "cover_style": "minimal",
        "header_rule": "false",
    },
}

DEFAULT_THEME = "default"

# 前端可调样式项（白名单：只接受这些键，值统一转字符串后进 CSS）
STYLE_WHITELIST = (
    "font_family",
    "font_size",
    "line_height",
    "text_align",
    "page_size",
    "page_orientation",
    "page_margin",
    "text_color",
    "heading_color",
    "link_color",
    "accent_color",
    "code_font_size",
    "theme",
)

DOCUMENT_DEFAULTS: dict[str, Any] = {
    "cover": True,
    "toc": True,
    "header": True,
    "footer": True,
    "page_number": True,
    "title": "",
    "subtitle": "",
    "author": "",
    "date": "",
}


def resolve_theme(name: str | None) -> dict[str, Any]:
    """取主题预设（未知主题回退默认），返回副本。"""
    key = (name or "").strip().lower()
    preset = THEME_PRESETS.get(key) or THEME_PRESETS[DEFAULT_THEME]
    merged = dict(preset)
    merged["key"] = key if key in THEME_PRESETS else DEFAULT_THEME
    return merged
