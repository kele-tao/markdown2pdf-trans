"""PDF 样式：主题 CSS + 文档级排版（封面/目录/页眉页脚/页码/双语）。"""
from __future__ import annotations

from typing import Any

from ..config import FONT_STACKS, is_rtl_language, resolve_theme
from .fonts import font_face_css, resolve_font_family


def _merge(style_options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(style_options or {})
    theme = resolve_theme(str(options.get("theme") or "default"))
    merged = {**theme, **{key: value for key, value in options.items() if value not in (None, "")}}
    return merged


def merge_style(style_options: dict[str, Any] | None) -> dict[str, Any]:
    """主题预设 + 用户覆盖，返回最终样式字典。"""
    return _merge(style_options)


def page_size_css(style: dict[str, Any]) -> str:
    size = str(style.get("page_size") or "A4").strip() or "A4"
    orientation = str(style.get("page_orientation") or "portrait").strip() or "portrait"
    if orientation not in ("portrait", "landscape"):
        orientation = "portrait"
    return f"{size} {orientation}"


def get_css_style(
    style_options: dict[str, Any] | None,
    *,
    document: dict[str, Any] | None = None,
    target_language: str = "",
    bilingual_layout: str = "",
    document_title: str = "",
    document_author: str = "",
) -> str:
    """生成完整样式表。

    - 页面级：尺寸/方向/边距/页码/页眉页脚（命名字符串在 @page 边框中渲染）
    - 内容级：标题阶梯、表格跨页重复表头、代码换行、图片不溢出、避免孤行
    - 双语：并排（两列，块级不跨页）与上下（原文区块 + 译文区块）
    """
    style = _merge(style_options)
    doc = dict(document or {})
    rtl = is_rtl_language(target_language)
    direction = "rtl" if rtl else "ltr"
    align = str(style.get("text_align") or "justify")
    if rtl and align == "justify":
        align = "right"

    font_family = str(style.get("font_family") or "").strip() or resolve_font_family(style, target_language)
    text_color = style.get("text_color", "#1f2328")
    muted = style.get("muted_color", "#5c6570")
    heading_color = style.get("heading_color", "#111418")
    link_color = style.get("link_color", "#1f5fbf")
    accent = style.get("accent_color", link_color)
    quote_bg = style.get("quote_bg_color", "#f5f7fa")
    quote_border = style.get("quote_border_color", "#c9d3e0")
    table_header_bg = style.get("table_header_bg", "#eef2f7")
    table_header_color = style.get("table_header_color", text_color)
    table_border = style.get("table_border_color", "#d5dce5")
    code_bg = style.get("code_bg", "#f6f8fa")
    rule_color = style.get("rule_color", "#dfe4ea")
    font_size = style.get("font_size", "11pt")
    line_height = style.get("line_height", "1.75")
    heading_weight = style.get("heading_weight", "700")
    code_font_size = style.get("code_font_size", "0.86em")
    margin = style.get("page_margin", "2.2cm 2cm")
    heading_font = FONT_STACKS.get(str(style.get("font_stack") or "serif"), FONT_STACKS["serif"])["family"]
    if rtl:
        heading_font = font_family
    mono = FONT_STACKS["mono"]["family"]

    cover = bool(doc.get("cover"))
    toc = bool(doc.get("toc"))
    header = bool(doc.get("header"))
    footer = bool(doc.get("footer"))
    page_number = bool(doc.get("page_number"))
    header_text = str(doc.get("header_text") or document_title or "")
    footer_text = str(doc.get("footer_text") or document_author or "")

    header_rule = str(style.get("header_rule", "true")).lower() != "false"

    header_box = ""
    if header and header_text:
        border = f"border-bottom: 0.5pt solid {rule_color}; padding-bottom: 3pt;" if header_rule else ""
        header_box = (
            "@top-center { content: string(doctitle); font-family: %s; font-size: 8.5pt; "
            "color: %s; %s }\n" % (font_family, muted, border)
        )
        header_box += "@top-right { content: string(sectiontitle); font-size: 8pt; color: %s; }\n" % muted

    footer_box = ""
    if footer and footer_text:
        footer_box += (
            "@bottom-left { content: '%s'; font-family: %s; font-size: 8.5pt; color: %s; }\n"
            % (_css_string(footer_text), font_family, muted)
        )
    if page_number:
        footer_box += (
            "@bottom-center { content: counter(page) ' / ' counter(pages); font-family: %s; "
            "font-size: 8.5pt; color: %s; }\n" % (font_family, muted)
        )

    cover_page_css = ""
    if cover:
        cover_page_css = """
    @page cover { margin: 0; @bottom-center { content: ''; } @top-center { content: ''; } @bottom-left { content: ''; } }
    .m2p-cover { page: cover; height: 100vh; display: flex; flex-direction: column;
                 justify-content: center; padding: 3cm 2.4cm; box-sizing: border-box;
                 break-after: page; }
    .m2p-cover .cover-kicker { font-size: 9px; letter-spacing: .28em; text-transform: uppercase;
                               color: %s; margin-bottom: 18mm; }
    .m2p-cover h1.cover-title { font-size: 30pt; line-height: 1.25; margin: 0 0 6mm;
                                color: %s; font-weight: 700; border: none; padding: 0; break-before: avoid; }
    .m2p-cover .cover-subtitle { font-size: 14pt; color: %s; margin-bottom: 24mm; line-height: 1.5; }
    .m2p-cover .cover-meta { font-size: 10pt; color: %s; line-height: 1.9; }
    .m2p-cover .cover-rule { width: 36mm; height: 3pt; background: %s; margin: 0 0 10mm; }
    """ % (muted, heading_color, muted, muted, accent)

    toc_css = ""
    if toc:
        toc_css = """
    .m2p-toc { break-after: page; }
    .m2p-toc h2.toc-title { font-size: 16pt; margin: 0 0 6mm; color: %s; }
    .m2p-toc ul { list-style: none; padding-left: 0; margin: 0; }
    .m2p-toc li { margin: 0; padding: 1.6mm 0; border-bottom: 0.4pt dotted %s; }
    .m2p-toc a { color: %s; text-decoration: none; }
    .m2p-toc a::after { content: target-counter(attr(href), page); float: right;
                        color: %s; font-variant-numeric: tabular-nums; }
    .m2p-toc .toc-l2 { padding-left: 6mm; font-size: 10.5pt; }
    .m2p-toc .toc-l3 { padding-left: 12mm; font-size: 10pt; }
    """ % (heading_color, rule_color, text_color, muted)

    return f"""
    {font_face_css()}
    @page {{
      size: {page_size_css(style)};
      margin: {margin};
      {header_box}
      {footer_box}
    }}
    {cover_page_css}
    {toc_css}
    html {{ font-size: {font_size}; }}
    body {{ font-family: {font_family}; line-height: {line_height}; color: {text_color};
            text-align: {align}; direction: {direction}; hyphens: auto;
            -weasy-hyphens: auto; margin: 0; }}
    h1, h2, h3, h4, h5, h6 {{ font-family: {heading_font}; color: {heading_color};
            font-weight: {heading_weight}; line-height: 1.3; text-align: {'right' if rtl else 'left'};
            break-after: avoid; break-inside: avoid; margin: 1.5em 0 .6em; }}
    h1 {{ font-size: 1.9em; border-bottom: 1pt solid {rule_color}; padding-bottom: .3em;
          string-set: doctitle content(); }}
    h2 {{ font-size: 1.45em; string-set: sectiontitle content(); }}
    h3 {{ font-size: 1.2em; }}
    h4 {{ font-size: 1.05em; }}
    p {{ margin: 0 0 .9em; orphans: 3; widows: 3; }}
    a {{ color: {link_color}; text-decoration: none; }}
    a[href^="http"]::after {{ content: ""; }}
    ul, ol {{ margin: 0 0 .9em; padding-{'right' if rtl else 'left'}: 1.4em; }}
    li {{ margin: .22em 0; }}
    img {{ max-width: 100%; height: auto; break-inside: avoid; display: block; margin: 1em auto; }}
    figure {{ margin: 1em 0; break-inside: avoid; }}
    figcaption {{ font-size: .85em; color: {muted}; text-align: center; margin-top: .4em; }}
    table {{ width: 100%; border-collapse: collapse; margin: 1.2em 0; font-size: .94em;
             break-inside: auto; }}
    thead {{ display: table-header-group; }}
    tfoot {{ display: table-footer-group; }}
    tr {{ break-inside: avoid; }}
    th, td {{ border: 0.6pt solid {table_border}; padding: .45em .6em; vertical-align: top;
              text-align: {'right' if rtl else 'left'}; }}
    th {{ background: {table_header_bg}; color: {table_header_color}; font-weight: 600; }}
    tbody tr:nth-child(even) td {{ background: rgba(0,0,0,.015); }}
    pre, code, tt {{ font-family: {mono}; font-size: {code_font_size}; }}
    pre {{ background: {code_bg}; border: 0.6pt solid {rule_color}; border-radius: 4pt;
           padding: .8em .9em; white-space: pre-wrap; word-wrap: break-word;
           overflow-wrap: break-word; break-inside: avoid; line-height: 1.55; }}
    code {{ background: {code_bg}; padding: .08em .3em; border-radius: 3pt; }}
    pre code {{ background: transparent; padding: 0; }}
    blockquote {{ margin: 1.1em 0; padding: .5em 1em; background: {quote_bg};
                  border-{'right' if rtl else 'left'}: 3pt solid {quote_border}; color: {muted};
                  break-inside: avoid; }}
    blockquote p:last-child {{ margin-bottom: 0; }}
    hr {{ border: none; border-top: 0.6pt solid {rule_color}; margin: 1.6em 0; }}
    .footnotes {{ font-size: .85em; color: {muted}; border-top: 0.6pt solid {rule_color};
                  margin-top: 2em; padding-top: .6em; }}
    .footnotes ol {{ padding-{'right' if rtl else 'left'}: 1.2em; }}
    .math {{ font-family: "Cambria Math", "Latin Modern Math", serif; }}
    .m2p-bilingual-side {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    .m2p-bilingual-side > tbody > tr {{ break-inside: avoid; }}
    .m2p-bilingual-side td {{ width: 50%; vertical-align: top; padding: 0 3mm;
                              border: none; background: transparent; }}
    .m2p-bilingual-side td + td {{ border-{'right' if rtl else 'left'}: 0.4pt solid {rule_color}; }}
    .m2p-bilingual-block {{ break-inside: avoid; margin: 0 0 .4em; }}
    .m2p-bilingual-stack .m2p-pair {{ break-inside: avoid; margin: 0 0 1.1em; }}
    .m2p-bilingual-stack .m2p-original {{ color: {muted}; font-size: .94em;
                                          padding-{'right' if rtl else 'left'}: 2.4mm;
                                          border-{'right' if rtl else 'left'}: 2pt solid {rule_color};
                                          margin-bottom: .35em; }}
    .m2p-bilingual-stack .m2p-translated {{ margin-bottom: .2em; }}
    .m2p-bilingual-label {{ font-size: .72em; letter-spacing: .12em; text-transform: uppercase;
                            color: {muted}; margin-bottom: .2em; }}
    [dir="rtl"] {{ text-align: right; }}
    .m2p-cover, .m2p-toc {{ direction: {direction}; }}
    """


def _css_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")