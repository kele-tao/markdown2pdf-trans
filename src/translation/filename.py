"""文件名翻译。

只翻译主文件名，保留扩展名；AI 不可用或调用失败时原样返回，绝不阻断主流程。
"""
from __future__ import annotations

import re
from typing import Callable

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_META_PREFIX = re.compile(
    r"^(?:here is|here's|translation|translated filename|以下是|译文|翻译结果)[^:：]{0,40}[:：]\s*",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = """你是文件名翻译助手。把用户给出的文件名（不含目录）翻译成 {target_language}。

规则：
1. 只输出翻译后的文件名文本，不要解释、不要引号、不要代码围栏。
2. 保留原有扩展名（如 .md、.pdf）；如果原文件名没有扩展名，就不要添加。
3. 空格用下划线代替；不要包含 / \\ : * ? " < > | 等非法字符。
4. 文件名要简洁，长度不超过 80 个字符。"""


def translate_filename(
    name: str,
    target_language: str,
    *,
    ai_call: Callable[[str, str], str] | None = None,
) -> str:
    """翻译文件名并保留扩展名；任何异常都回退为原文件名。"""
    original = name or ""
    if not original.strip() or ai_call is None:
        return original

    prefix, base = _split_prefix(original)
    if base.startswith(".") and base.count(".") == 1:
        return original  # 隐藏文件（如 .env）保持原样
    stem, extension = _split_extension(base)
    if not stem.strip():
        return original

    try:
        raw = ai_call(
            _SYSTEM_PROMPT.format(target_language=(target_language or "").strip() or "目标语言"),
            f"文件名：{base}\n需要翻译的部分：{stem}",
        )
    except Exception:
        return original

    translated = _clean_model_name(raw)
    translated = _strip_extension(translated, extension)
    if extension:
        translated = _strip_foreign_extension(translated)
    cleaned = _sanitize(translated)
    if not cleaned:
        return original

    result = cleaned + extension
    if len(prefix + result) > 180:
        return original
    return prefix + result


def _split_prefix(name: str) -> tuple[str, str]:
    """拆出目录前缀（保留原分隔符）与文件名。"""
    match = re.match(r"^(?P<prefix>.*[\\/])?(?P<base>[^\\/]+)$", name)
    if not match:
        return "", name
    return match.group("prefix") or "", match.group("base")


def _split_extension(base: str) -> tuple[str, str]:
    if base.startswith(".") and base.count(".") == 1:
        return base, ""
    index = base.rfind(".")
    if index <= 0 or index == len(base) - 1:
        return base, ""
    extension = base[index:]
    if len(extension) > 16:
        return base, ""
    return base[:index], extension


def _clean_model_name(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.replace("\r\n", "\n").replace("\r", "\n").strip().strip("\ufeff").strip()
    value = value.split("\n")[0].strip()
    value = value.strip("\"'`“”‘’")
    value = _META_PREFIX.sub("", value).strip()
    return value


def _strip_extension(value: str, extension: str) -> str:
    if extension and value.casefold().endswith(extension.casefold()):
        return value[: -len(extension)]
    return value


def _strip_foreign_extension(value: str) -> str:
    """原文有扩展名时，去掉模型额外带上的其他字母扩展名。"""
    return re.sub(r"\.[A-Za-z]{1,8}$", "", value)


def _sanitize(value: str) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("", value or "")
    cleaned = cleaned.replace(" ", "_").replace("\t", "_")
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip(" ._-")
    return cleaned[:80]
