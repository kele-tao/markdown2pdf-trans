"""
PDF 渲染层：
- 集中处理 Markdown → HTML → PDF
- 显式捕获 Pandoc / WeasyPrint 异常，包装成 RenderError
- 集中管理 CSS 样式生成
- 集中管理图片预处理（base64 嵌入）
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Optional

from .config import THEME_PRESETS

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """PDF 渲染过程中的受控异常。"""

    def __init__(self, message: str, kind: str = "render") -> None:
        super().__init__(message)
        self.kind = kind  # pandoc | weasyprint | image | unknown


# 单图嵌入上限，可通过环境变量 M2P_MAX_IMAGE_BYTES 调整
import os as _os

_MAX_IMAGE_BYTES = int(_os.environ.get("M2P_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))


def preprocess_markdown_images(md_content: str, md_file_dir: Path) -> str:
    """把本地相对路径的图片转成 base64 data URI，让 PDF 能内联显示。"""

    def replacer(match: re.Match) -> str:
        alt_text, link = match.group(1), match.group(2)
        if link.startswith(("http://", "https://", "data:image")):
            return match.group(0)
        clean_link = link.split("?")[0]
        absolute_image_path = (md_file_dir / clean_link).resolve()
        try:
            absolute_image_path.relative_to(md_file_dir.resolve())
        except ValueError:
            return match.group(0)
        if not absolute_image_path.is_file():
            return match.group(0)
        try:
            mime_type, _ = mimetypes.guess_type(str(absolute_image_path))
            if not mime_type:
                mime_type = "application/octet-stream"
            img_data = absolute_image_path.read_bytes()
            if len(img_data) > _MAX_IMAGE_BYTES:
                logger.warning("Image too large (>%d bytes), skipped: %s", _MAX_IMAGE_BYTES, absolute_image_path)
                return match.group(0)
            base64_data = base64.b64encode(img_data).decode("ascii")
            return f"![{alt_text}](data:{mime_type};base64,{base64_data})"
        except OSError as e:
            logger.warning("Failed to embed image %s: %s", absolute_image_path, e)
            return match.group(0)

    return re.sub(r"!\[(.*?)\]\((.*?)\)", replacer, md_content)


def get_css_style(style_options: dict[str, Any], extra: str = "") -> str:
    """根据 style_options 拼出 WeasyPrint 的 CSS。"""
    # 优先用 THEME_PRESETS 中的值，再用 style_options 覆盖
    preset = THEME_PRESETS.get("default", {})
    merged = {**preset, **style_options}

    def get(key: str, default: str) -> str:
        return str(merged.get(key, default))

    return f"""
        @page {{ size: A4 {get('page_orientation', 'portrait')}; margin: {get('page_margin', '2.5cm')}; }}
        html {{ font-size: {get('font_size', '12pt')}; }}
        body {{ font-family: {get('font_family', 'serif')}; line-height: {get('line_height', '1.7')};
                color: {get('text_color', '#333')}; text-align: {get('text_align', 'justify')}; }}
        a {{ color: {get('link_color', '#0d6efd')}; text-decoration: none; }}
        h1,h2,h3,h4,h5,h6 {{ color: {get('heading_color', '#000')}; text-align: left;
                            font-weight: {get('heading_weight', '700')}; }}
        img {{ max-width: 100%; height: auto; }}
        table {{ width: 100%; border-collapse: collapse; margin: 1.5em 0; }}
        th,td {{ border: 1px solid #ccc; padding: .75em; }}
        pre, code, tt {{ font-size: {get('code_font_size', '85%')}; }}
        pre {{ white-space: pre-wrap; }}
        blockquote {{ margin: 1.5em 0; padding: .5em 1.5em;
                      background-color: {get('quote_bg_color', '#f9f9f9')};
                      border-left: 5px solid {get('quote_border_color', '#ccc')}; }}
        .bilingual-table {{ width: 100%; table-layout: fixed; border-collapse: collapse; }}
        .bilingual-table td {{ width: 50%; vertical-align: top; padding: 5px 10px; border: 1px solid #eee; }}
        {extra}
    """


def md_to_html(md_content: str, md_file_dir: Path, code_theme: str = "kate") -> str:
    """Markdown → HTML。失败抛 RenderError。"""
    try:
        import pypandoc  # 延迟导入
    except ImportError as e:
        raise RenderError(f"pypandoc 未安装: {e}", kind="pandoc") from e

    processed = preprocess_markdown_images(md_content, md_file_dir)
    try:
        return pypandoc.convert_text(
            source=processed,
            to="html",
            format="markdown+latex_macros",
            extra_args=[f"--highlight-style={code_theme}"],
        )
    except Exception as e:  # pypandoc 没有定义专用异常
        raise RenderError(f"Pandoc 转换失败: {e}", kind="pandoc") from e


def html_to_pdf_bytes(html_body: str, style_options: dict[str, Any]) -> bytes:
    """HTML → PDF 字节流。失败抛 RenderError。"""
    try:
        import weasyprint  # 延迟导入
    except ImportError as e:
        raise RenderError(f"weasyprint 未安装: {e}", kind="weasyprint") from e
    try:
        css = weasyprint.CSS(string=get_css_style(style_options))
        doc = weasyprint.HTML(string=f"<html><body>{html_body}</body></html>")
        return doc.write_pdf(stylesheets=[css])
    except Exception as e:
        raise RenderError(f"WeasyPrint 渲染失败: {e}", kind="weasyprint") from e


def render_markdown_to_pdf(
    md_content: str,
    md_file_dir: Path,
    output_path: Path,
    style_options: dict[str, Any],
) -> None:
    """完整的 md → PDF 文件流程。"""
    html = md_to_html(md_content, md_file_dir, style_options.get("code_theme", "kate"))
    pdf_bytes = html_to_pdf_bytes(html, style_options)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
