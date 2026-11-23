"""翻译引擎测试共用工具：从提示词中取出待翻译块、构造假 AI 调用。"""
from __future__ import annotations

import pathlib
import re
import time
from typing import Callable

from src.translation.prompts import CONTENT_BEGIN, CONTENT_END

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "translation"


def load_fixture(name: str = "long_document.md") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def extract_payload(user_content: str) -> str:
    """取出提示词中 <<<TRANSLATE_BEGIN>>> 与 <<<TRANSLATE_END>>> 之间的原文块。"""
    start = user_content.index(CONTENT_BEGIN) + len(CONTENT_BEGIN)
    end = user_content.index(CONTENT_END, start)
    return user_content[start:end].strip("\n")


def marker_delay(payload: str, scale: float = 0.01) -> float:
    """让靠后的块先完成，用于验证并发下仍按原文顺序拼回。"""
    match = re.search(r"ALPHA(\d+)", payload)
    if not match:
        return 0.0
    return scale * max(0, 17 - int(match.group(1)))


def build_translator(
    mapping: dict[str, str] | None = None,
    *,
    calls: list[str] | None = None,
    prompts: list[str] | None = None,
    delay: Callable[[str], float] | None = None,
    on_call: Callable[[str, str], None] | None = None,
) -> Callable[[str, str], str]:
    """构造假 AI：从提示词取出原文块，按映射替换后原样返回，保持结构不变。"""
    items = sorted((mapping or {}).items(), key=lambda item: -len(item[0]))

    def ai_call(system_prompt: str, user_content: str) -> str:
        payload = extract_payload(user_content)
        if calls is not None:
            calls.append(payload)
        if prompts is not None:
            prompts.append(system_prompt)
        if on_call is not None:
            on_call(system_prompt, payload)
        if delay is not None:
            wait = delay(payload)
            if wait > 0:
                time.sleep(wait)
        result = payload
        for source, target in items:
            result = result.replace(source, target)
        return result

    return ai_call


def long_document_mapping() -> dict[str, str]:
    """长文样例句级标记映射：ALPHA10 -> T10（编号零填充，便于定位顺序）。"""
    mapping = {f"ALPHA{index}": f"T{index:02d}" for index in range(1, 17)}
    mapping.update(
        {
            "Deployment Guide": "部署指南",
            "Installation": "安装",
            "Requirements": "运行要求",
            "Configuration": "配置",
            "Verification": "验证",
            "Long paragraph": "长段落",
            "Prepare the server": "准备服务器",
            "Install the package": "安装软件包",
            "Verify the service": "验证服务",
        }
    )
    return mapping
