"""HTML → PDF（WeasyPrint 封装）。"""
from __future__ import annotations

import logging
from typing import Any

from ..config import is_rtl_language
from .css import get_css_style
from .fonts import register_fontconfig
from .html import RenderError

logger = logging.getLogger("m2p.pdf")


def html_to_pdf_bytes(
    html_body: str,
    style_options: dict[str, Any] | None,
    *,
    document: dict[str, Any] | None = None,
    target_language: str = "",
    bilingual_layout: str = "",
    document_title: str = "",
    document_author: str = "",
    base_url: str | None = None,
) -> bytes:
    """渲染 PDF 字节流。失败抛 RenderError。"""
    try:
        import weasyprint
    except ImportError as exc:  # pragma: no cover
        raise RenderError(f"weasyprint 未安装：{exc}", kind="weasyprint") from exc

    register_fontconfig()
    css_text = get_css_style(
        style_options,
        document=document,
        target_language=target_language,
        bilingual_layout=bilingual_layout,
        document_title=document_title,
        document_author=document_author,
    )
    direction = "rtl" if is_rtl_language(target_language) else "ltr"
    lang = (target_language or "en").lower()
    markup = (
        f'<!DOCTYPE html><html lang="{lang}" dir="{direction}">'
        "<head><meta charset=\"utf-8\"></head>"
        f"<body>{html_body}</body></html>"
    )
    try:
        stylesheet = weasyprint.CSS(string=css_text)
        doc = weasyprint.HTML(string=markup, base_url=base_url)
        return doc.write_pdf(stylesheets=[stylesheet])
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"WeasyPrint 渲染失败：{exc}", kind="weasyprint") from exc
