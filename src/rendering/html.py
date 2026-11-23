"""Markdown → HTML（pandoc）+ 图片内联 + 目录提取与封面拼装。"""
from __future__ import annotations

import base64
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from ..config import MAX_IMAGE_BYTES, is_rtl_language

logger = logging.getLogger("m2p.render")

_IMG_RE = re.compile(r'!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?P<title>\s+("[^"]*"|\x27[^\x27]*\x27))?\s*\)')
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<text>.*?)[ \t]*#*[ \t]*$")
_HTML_ESCAPE = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
_PANDOC_VERSION: tuple[int, int] | None = None


class RenderError(Exception):
    """PDF 渲染过程中的受控异常。"""

    def __init__(self, message: str, kind: str = "render") -> None:
        super().__init__(message)
        self.kind = kind  # pandoc | weasyprint | image | unknown


def escape_html(value: str) -> str:
    return "".join(_HTML_ESCAPE.get(char, char) for char in str(value or ""))


def preprocess_markdown_images(md_content: str, md_file_dir: Path) -> str:
    """把本地相对路径图片转成 data URI，保证 PDF 自包含。"""

    def replacer(match: re.Match[str]) -> str:
        alt_text, link, title = match.group("alt"), match.group("url"), match.group("title") or ""
        if link.startswith(("http://", "https://", "data:image")):
            return match.group(0)
        clean_link = link.split("?")[0].split("#")[0]
        absolute = (md_file_dir / clean_link).resolve()
        try:
            absolute.relative_to(md_file_dir.resolve())
        except ValueError:
            logger.warning("图片路径越界，已跳过：%s", link)
            return match.group(0)
        if not absolute.is_file():
            return match.group(0)
        try:
            payload = absolute.read_bytes()
        except OSError as exc:
            logger.warning("读取图片失败 %s: %s", absolute, exc)
            return match.group(0)
        if len(payload) > MAX_IMAGE_BYTES:
            logger.warning("图片超过 %d 字节上限，已跳过：%s", MAX_IMAGE_BYTES, absolute)
            return match.group(0)
        mime = mimetypes.guess_type(str(absolute))[0] or "application/octet-stream"
        encoded = base64.b64encode(payload).decode("ascii")
        return f"![{alt_text}](data:{mime};base64,{encoded}{title})"

    return _IMG_RE.sub(replacer, md_content)


def extract_headings(md_content: str) -> list[dict[str, Any]]:
    """提取用于目录的标题（跳过围栏代码块内）。"""
    headings: list[dict[str, Any]] = []
    in_fence = False
    fence_marker = ""
    for index, line in enumerate(md_content.replace("\r\n", "\n").split("\n")):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group("hashes"))
        text = match.group("text").strip()
        if not text:
            continue
        headings.append({"level": level, "text": text, "id": f"m2p-h-{len(headings) + 1}", "line": index})
    return headings


def add_heading_ids(html: str) -> str:
    """给 pandoc 输出的 h1..h6 按文档顺序补 id，供目录 target-counter 精确定位。"""
    counter = {"n": 0}

    def replacer(match: re.Match[str]) -> str:
        counter["n"] += 1
        level, attrs, body = match.group(1), match.group(2) or "", match.group(3)
        if re.search(r"\sid\s*=", attrs):
            return match.group(0)  # 已有 id（pandoc 生成的），保留原值
        return f'<h{level}{attrs} id="m2p-h-{counter["n"]}">{body}</h{level}>'

    return re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", replacer, html, flags=re.DOTALL)


def build_toc_html(headings: list[dict[str, Any]], *, title: str = "目录") -> str:
    if not headings:
        return ""
    items: list[str] = []
    for heading in headings:
        level = int(heading["level"])
        css_class = "toc-l2" if level == 2 else "toc-l3" if level >= 3 else ""
        items.append(
            f'<li class="{css_class}"><a href="#{escape_html(heading["id"])}">'
            f'{escape_html(heading["text"])}</a></li>'
        )
    return (
        f'<section class="m2p-toc"><h2 class="toc-title">{escape_html(title)}</h2>'
        f'<ul>{"".join(items)}</ul></section>'
    )


def build_cover_html(
    *,
    title: str,
    subtitle: str = "",
    author: str = "",
    date: str = "",
    kicker: str = "MARKDOWN PDF",
) -> str:
    if not title and not subtitle:
        return ""
    parts = ['<section class="m2p-cover">']
    if kicker:
        parts.append(f'<div class="cover-kicker">{escape_html(kicker)}</div>')
    parts.append('<div class="cover-rule"></div>')
    if title:
        parts.append(f'<h1 class="cover-title">{escape_html(title)}</h1>')
    if subtitle:
        parts.append(f'<div class="cover-subtitle">{escape_html(subtitle)}</div>')
    meta = [escape_html(value) for value in (author, date) if value]
    if meta:
        parts.append('<div class="cover-meta">' + "<br>".join(meta) + "</div>")
    parts.append("</section>")
    return "".join(parts)


def md_to_html(
    md_content: str,
    md_file_dir: Path,
    code_theme: str = "kate",
    *,
    target_language: str = "",
) -> str:
    """Markdown → HTML 片段。失败抛 RenderError。"""
    try:
        import pypandoc
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise RenderError(f"pypandoc 未安装：{exc}", kind="pandoc") from exc

    processed = preprocess_markdown_images(md_content, md_file_dir)
    allowed = {"kate", "pygments", "tango", "monokai", "zenburn", "espresso", "breezedark", "haddock"}
    style = code_theme if code_theme in allowed else "kate"
    extra_args = [_highlight_arg(style), f"--metadata=lang:{_lang_tag(target_language)}"]
    try:
        return pypandoc.convert_text(
            source=processed,
            to="html",
            format="markdown+tex_math_dollars+footnotes+pipe_tables+fenced_divs+raw_html",
            extra_args=extra_args,
        )
    except Exception as exc:  # pypandoc 未定义专用异常
        raise RenderError(f"Pandoc 转换失败：{exc}", kind="pandoc") from exc


def _highlight_arg(style: str) -> str:
    """pandoc 3.2+ 用 --syntax-highlighting，旧版用 --highlight-style。"""
    major, minor = _pandoc_version()
    if (major, minor) >= (3, 2):
        return f"--syntax-highlighting={style}"
    return f"--highlight-style={style}"


def _pandoc_version() -> tuple[int, int]:
    global _PANDOC_VERSION
    if _PANDOC_VERSION is None:
        try:
            import pypandoc

            raw = str(pypandoc.get_pandoc_version())
            parts = re.findall(r"\d+", raw)
            _PANDOC_VERSION = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except Exception:  # pragma: no cover
            _PANDOC_VERSION = (3, 0)
    return _PANDOC_VERSION


def _lang_tag(language: str) -> str:
    mapping = {"english": "en", "chinese": "zh", "french": "fr", "russian": "ru", "spanish": "es", "arabic": "ar"}
    return mapping.get((language or "").strip().casefold(), "en")


def is_rtl(language: str) -> bool:
    return is_rtl_language(language)
