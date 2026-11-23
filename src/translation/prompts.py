"""翻译提示词与提示词版本。

提示词版本参与缓存 key：规则调整后旧缓存自然失效。
"""
from __future__ import annotations

from .markdown_parser import BLOCK_LABELS, BlockKind
from .models import GlossaryEntry

PROMPT_VERSION = "translation-v3.1"

CONTENT_BEGIN = "<<<TRANSLATE_BEGIN>>>"
CONTENT_END = "<<<TRANSLATE_END>>>"

TONE_INSTRUCTIONS: dict[str, str] = {
    "professional": "专业、准确、简洁的商务与技术文体，避免口语化表达",
    "academic": "严谨的学术文体，术语前后一致，句式完整，避免口语与感叹",
    "casual": "自然亲切的口语文体，表达流畅易懂，但不得改动事实与结构",
}

_SYSTEM_TEMPLATE = """你是一位资深专业译者，负责把 Markdown 文档片段翻译成 {target_language}。

文体要求：{tone_instruction}

严格执行以下规则：
1. 只输出译文本身。不要解释、不要前言、不要“以下是译文”之类的提示，不要用代码围栏包裹整段译文。
2. 完整保留 Markdown 结构：标题级别与数量、段落数量、列表项数量与嵌套层级、表格的列数与行数、引用标记、水平线、空行结构。
3. 以下内容必须原样保留，绝不翻译：围栏代码块内容、行内代码、链接与图片的 URL/路径、HTML 标签、占位符（如 {{{{name}}}}、${{var}}、%s）、LaTeX 公式。
4. 链接与图片只翻译方括号中的可见文字，圆括号中的地址保持不变。
5. 本次只处理一个 Markdown 块：输入是标题就输出同级别标题，输入是列表就输出列表，输入是表格就输出表格，输入是段落就输出段落。不得合并或拆分块。
6. 译文必须通顺、准确，符合 {target_language} 的表达习惯。
{glossary_section}"""


def resolve_tone(tone: str) -> str:
    value = (tone or "").strip().lower()
    return value if value in TONE_INSTRUCTIONS else "professional"


def build_system_prompt(
    target_language: str,
    tone: str,
    glossary: list[GlossaryEntry],
) -> str:
    return _SYSTEM_TEMPLATE.format(
        target_language=target_language.strip(),
        tone_instruction=TONE_INSTRUCTIONS[resolve_tone(tone)],
        glossary_section=_glossary_section(glossary),
    )


def build_user_content(
    *,
    title: str,
    summary: str,
    path: tuple[str, ...],
    previous: str,
    following: str,
    kind: BlockKind,
    part_index: int,
    part_total: int,
    text: str,
) -> str:
    lines: list[str] = ["【文档信息】"]
    if title:
        lines.append(f"文档标题：{title}")
    if summary and summary != title:
        lines.append(f"文档摘要：{summary}")
    if path:
        lines.append(f"章节路径：{' > '.join(path)}")
    else:
        lines.append("章节路径：（文档开头，无上级标题）")
    lines.append(f"当前块类型：{BLOCK_LABELS.get(kind, '文本')}")
    if part_total > 1:
        lines.append(
            f"分段信息：该段落较长，已按句子边界分为 {part_total} 段，当前是第 {part_index} 段；"
            "只翻译本段，不要补充上下文，也不要省略内容。"
        )
    if previous:
        lines.extend(["", "【上一块（仅用于理解上下文，不要翻译）】", previous])
    if following:
        lines.extend(["", "【下一块（仅用于理解上下文，不要翻译）】", following])
    lines.extend(["", "【当前块原文】", CONTENT_BEGIN, text, CONTENT_END])
    return "\n".join(lines)


def build_retry_note(issues: list[str]) -> str:
    detail = "；".join(issues[:5]) if issues else "译文结构不完整"
    return (
        "\n\n【重试要求】上一次译文存在以下问题："
        + detail
        + "。请重新翻译上面的当前块，严格保持原有 Markdown 结构，只输出译文。"
    )


def _glossary_section(entries: list[GlossaryEntry]) -> str:
    if not entries:
        return ""
    lines = [
        "",
        "【术语表】以下术语必须使用指定译法（匹配不区分大小写、按整词匹配；同一术语在全文中保持一致）：",
    ]
    lines.extend(f"- {entry.source} → {entry.target}" for entry in entries)
    return "\n".join(lines)
