"""渲染门面：把 Markdown 渲染成完整 PDF（封面 + 目录 + 正文）。

保留对旧的 `src.renderer` 接口兼容：get_css_style / md_to_html / html_to_pdf_bytes /
render_markdown_to_pdf / RenderError。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import resolve_theme
from .rendering.bilingual import build_bilingual_html
from .rendering.css import get_css_style, merge_style, page_size_css  # noqa: F401
from .rendering.html import (  # noqa: F401
    RenderError,
    build_cover_html,
    build_toc_html,
    extract_headings,
    md_to_html,
    preprocess_markdown_images,
)
from .rendering.pdf import html_to_pdf_bytes  # noqa: F401

logger = logging.getLogger("m2p.renderer")


def compose_document_html(
    md_content: str,
    md_file_dir: Path,
    *,
    style_options: dict[str, Any] | None = None,
    document: dict[str, Any] | None = None,
    target_language: str = "",
    fallback_title: str = "",
) -> str:
    """把 Markdown 拼成带封面/目录/正文的完整 HTML。"""
    style = merge_style(style_options)
    doc = dict(document or {})
    headings = extract_headings(md_content)
    title = str(doc.get("title") or "").strip() or (headings[0]["text"] if headings else fallback_title)
    author = str(doc.get("author") or "").strip()
    subtitle = str(doc.get("subtitle") or "").strip()
    date = str(doc.get("date") or "").strip()

    body_html = md_to_html(
        md_content,
        md_file_dir,
        style.get("code_theme", "kate"),
        target_language=target_language,
    )

    parts: list[str] = []
    if doc.get("cover"):
        parts.append(
            build_cover_html(title=title, subtitle=subtitle, author=author, date=date)
        )
    if doc.get("toc"):
        parts.append(build_toc_html(headings, title="目录"))
    parts.append(f'<main class="m2p-body">{body_html}</main>')

    doc_meta = dict(doc)
    doc_meta.setdefault("header_text", title)
    doc_meta["title"] = title
    # 供 @page 页眉使用
    html = "".join(parts)
    return html, doc_meta  # type: ignore[return-value]


def render_markdown_to_pdf(
    md_content: str,
    md_file_dir: Path,
    output_path: Path,
    style_options: dict[str, Any] | None = None,
    *,
    document: dict[str, Any] | None = None,
    target_language: str = "",
    fallback_title: str = "",
) -> bytes:
    """完整的 md → PDF 文件流程。返回 PDF 字节。"""
    html, doc_meta = compose_document_html(
        md_content,
        md_file_dir,
        style_options=style_options,
        document=document,
        target_language=target_language,
        fallback_title=fallback_title,
    )
    pdf = html_to_pdf_bytes(
        html,
        style_options,
        document=doc_meta,
        target_language=target_language,
        document_title=doc_meta.get("title", ""),
        document_author=doc_meta.get("author", ""),
        base_url=str(md_file_dir),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf)
    return pdf


def render_bilingual_to_pdf(
    original_md: str,
    translated_md: str,
    md_file_dir: Path,
    output_path: Path,
    style_options: dict[str, Any] | None = None,
    *,
    document: dict[str, Any] | None = None,
    target_language: str = "",
    layout: str = "side_by_side",
    fallback_title: str = "",
) -> bytes:
    """双语对照 PDF。封面/目录按原文提取，正文为成对块。"""
    style = merge_style(style_options)
    doc = dict(document or {})
    headings = extract_headings(original_md)
    title = str(doc.get("title") or "").strip() or (headings[0]["text"] if headings else fallback_title)
    doc_meta = dict(doc)
    doc_meta["title"] = title
    doc_meta.setdefault("header_text", title)

    body = build_bilingual_html(
        original_md,
        translated_md,
        md_file_dir,
        layout=layout,
        code_theme=style.get("code_theme", "kate"),
        target_language=target_language,
        original_label=str(doc.get("original_label") or "原文"),
        translated_label=str(doc.get("translated_label") or "译文"),
    )
    parts: list[str] = []
    if doc.get("cover"):
        parts.append(
            build_cover_html(
                title=title,
                subtitle=str(doc.get("subtitle") or ""),
                author=str(doc.get("author") or ""),
                date=str(doc.get("date") or ""),
            )
        )
    if doc.get("toc"):
        parts.append(build_toc_html(headings, title="目录"))
    parts.append(f'<main class="m2p-body">{body}</main>')
    pdf = html_to_pdf_bytes(
        "".join(parts),
        style_options,
        document=doc_meta,
        target_language=target_language,
        bilingual_layout=layout,
        document_title=title,
        document_author=str(doc.get("author") or ""),
        base_url=str(md_file_dir),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf)
    return pdf
