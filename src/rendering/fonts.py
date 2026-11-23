"""字体注册与字体族解析。

WeasyPrint 使用 fontconfig 查找字体。为了让"换机器输出一致"，我们把
`fonts/` 目录下的字体注册给 fontconfig（Linux 用 FONTCONFIG_FILE 临时配置，
Windows 直接可用绝对路径 @font-face）。渲染层再按目标语言挑选字体链。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ..config import FONT_DIR, FONT_FILES, FONT_STACKS, is_rtl_language

logger = logging.getLogger("m2p.fonts")

_FONT_FACE_NAMES = {
    "NotoSerifSC-VF.ttf": "M2P Serif CJK",
    "NotoSansSC-VF.ttf": "M2P Sans CJK",
    "NotoSansArabic-Regular.ttf": "M2P Sans Arabic",
    "NotoNaskhArabic-Regular.ttf": "M2P Naskh Arabic",
}


def available_font_files() -> list[Path]:
    """返回实际存在的本地字体文件。"""
    found: list[Path] = []
    for name in FONT_FILES:
        candidate = FONT_DIR / name
        if candidate.is_file():
            found.append(candidate)
    if FONT_DIR.is_dir():
        for path in sorted(FONT_DIR.glob("*.ttf")):
            if path not in found:
                found.append(path)
        for path in sorted(FONT_DIR.glob("*.otf")):
            if path not in found:
                found.append(path)
    return found


def font_face_css() -> str:
    """生成 @font-face 规则，让 WeasyPrint 内嵌本地字体。"""
    rules: list[str] = []
    for path in available_font_files():
        family = _FONT_FACE_NAMES.get(path.name, "M2P " + path.stem.replace("-", " "))
        weight = "700" if "Bold" in path.name else "400"
        rules.append(
            "@font-face { font-family: '%s'; src: url('%s'); font-weight: %s; font-style: normal; }"
            % (family, path.as_uri(), weight)
        )
    return "\n".join(rules)


def resolve_font_family(theme: dict[str, Any], target_language: str) -> str:
    """按主题 + 目标语言挑选字体族。

    阿拉伯语优先 Naskh 字体；中文需 CJK 字体排在拉丁字体之后（同优先级下
    WeasyPrint 会逐字符回退）。
    """
    stack_key = str(theme.get("font_stack") or "serif")
    stack = FONT_STACKS.get(stack_key, FONT_STACKS["serif"])["family"]
    if is_rtl_language(target_language):
        return (
            '"M2P Naskh Arabic", "M2P Sans Arabic", "Noto Naskh Arabic", "Noto Sans Arabic", '
            '"Tahoma", "Segoe UI", ' + stack
        )
    if "chinese" in (target_language or "").casefold() or "中文" in (target_language or ""):
        return (
            '"M2P Serif CJK", "M2P Sans CJK", "Noto Serif SC", "Noto Sans SC", '
            '"Microsoft YaHei", "SimSun", ' + stack
        )
    return stack


def register_fontconfig() -> None:
    """把 fonts/ 目录加入 fontconfig 搜索路径（Linux/Docker 生效）。"""
    if sys.platform == "win32":
        return
    if not FONT_DIR.is_dir():
        return
    existing = os.environ.get("FONTCONFIG_PATH")
    conf_file = FONT_DIR / "fonts.conf"
    if not conf_file.is_file():
        conf_file.write_text(
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
            "<fontconfig>\n"
            f"  <dir>{FONT_DIR.as_posix()}</dir>\n"
            "  <cachedir>/tmp/fontconfig-cache</cachedir>\n"
            "</fontconfig>\n",
            encoding="utf-8",
        )
    logger.debug("fontconfig 配置位于 %s（原 FONTCONFIG_PATH=%s）", conf_file, existing)


def font_status() -> dict[str, Any]:
    files = available_font_files()
    return {
        "dir": str(FONT_DIR),
        "count": len(files),
        "files": [path.name for path in files],
        "system_fallback": True,
    }
