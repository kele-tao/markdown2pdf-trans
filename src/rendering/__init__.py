"""渲染层：字体、样式、Markdown→HTML、HTML→PDF、双语拼装。"""
from .css import get_css_style, merge_style, page_size_css  # noqa: F401
from .fonts import font_status, resolve_font_family  # noqa: F401
from .html import (  # noqa: F401
    RenderError,
    build_cover_html,
    build_toc_html,
    escape_html,
    extract_headings,
    md_to_html,
    preprocess_markdown_images,
)
from .pdf import html_to_pdf_bytes  # noqa: F401
