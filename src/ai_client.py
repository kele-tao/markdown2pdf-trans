"""AI 客户端：真实（OpenAI 兼容）与离线 mock 双通道。

对外只暴露 ai_call(system_prompt, user_content) -> str，供翻译引擎注入。
- 真实通道：指数退避重试 + 并发信号量 + 块级缓存
- mock 通道：不依赖网络，模拟"按块结构翻译"，保证无密钥也能演示全流程
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from typing import Callable, Optional

import requests

from .concurrency import RateLimiter
from .core.settings_store import get_settings
from .translation.markdown_parser import BlockKind, parse_markdown
from .translation.prompts import CONTENT_BEGIN, CONTENT_END
from .translation_cache import TranslationCache

logger = logging.getLogger("m2p.ai")


class AIError(Exception):
    """可分类的 AI 调用错误，供上层决定如何重试/降级。"""

    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind  # network | auth | rate_limit | parse | config | unknown


_MOCK_DICTIONARY: dict[str, dict[str, str]] = {
    "English": {
        "文档": "Document",
        "安装": "Installation",
        "配置": "Configuration",
        "验证": "Verification",
        "部署": "Deployment",
        "运行要求": "Requirements",
        "准备服务器": "Prepare the server",
        "安装软件包": "Install the package",
        "验证服务": "Verify the service",
        "这是一个示例段落，用于演示翻译流程。": "This is a sample paragraph used to demonstrate the translation workflow.",
        "第一项": "First item",
        "第二项": "Second item",
        "重要提示": "Important note",
    },
    "Chinese": {
        "Installation": "安装",
        "Configuration": "配置",
        "Verification": "验证",
        "Deployment": "部署",
        "Requirements": "运行要求",
        "Prepare the server": "准备服务器",
        "Install the package": "安装软件包",
        "Verify the service": "验证服务",
        "This is a sample paragraph used to demonstrate the translation workflow.": "这是一个示例段落，用于演示翻译流程。",
        "Important note": "重要提示",
    },
}

_MOCK_LANGUAGE_LABEL = {
    "English": "English",
    "Chinese": "中文",
    "French": "Français",
    "Russian": "Русский",
    "Spanish": "Español",
    "Arabic": "العربية",
}

_MOCK_LANG_CODE = {
    "English": "en",
    "Chinese": "zh",
    "French": "fr",
    "Russian": "ru",
    "Spanish": "es",
    "Arabic": "ar",
}

_MOCK_PREFIX = {
    "French": "【FR】",
    "Russian": "【RU】",
    "Spanish": "【ES】",
    "Arabic": "【AR】",
}


def extract_payload(user_content: str) -> str:
    """从提示词中取出待翻译块。取不到时返回整个输入。"""
    if CONTENT_BEGIN in user_content and CONTENT_END in user_content:
        start = user_content.index(CONTENT_BEGIN) + len(CONTENT_BEGIN)
        end = user_content.index(CONTENT_END, start)
        return user_content[start:end].strip("\n")
    return user_content


def detect_target_language(system_prompt: str) -> str:
    match = re.search(r"翻译成\s*([^\s，。、；]+)", system_prompt)
    if match:
        return match.group(1).strip()
    return "English"


def _mock_translate_block(payload: str, language: str) -> str:
    """离线翻译：保留 Markdown 结构，替换文本内容。

    目标不是"译得准"，而是保证结构保真校验可以通过，并让演示内容可读。
    """
    dictionary = _MOCK_DICTIONARY.get(language, {})
    label = _MOCK_LANGUAGE_LABEL.get(language, language)
    prefix = _MOCK_PREFIX.get(language, "")

    parsed = parse_markdown(payload)
    if not parsed.blocks:
        return payload

    pieces: list[str] = []
    for block in parsed.blocks:
        pieces.append(block.gap)
        if block.kind is BlockKind.HEADING:
            text = _mock_text(block.text.strip(), dictionary, label, prefix, heading=True)
            pieces.append(text)
        elif block.kind in (BlockKind.CODE, BlockKind.FRONT_MATTER, BlockKind.MATH, BlockKind.HR):
            pieces.append(block.text)
        elif block.kind is BlockKind.TABLE:
            pieces.append(_mock_table(block.text, dictionary, label, prefix))
        else:
            pieces.append(_mock_text(block.text, dictionary, label, prefix))
    pieces.append(parsed.trailer)
    return "".join(pieces)


def _mock_text(text: str, dictionary: dict[str, str], label: str, prefix: str, heading: bool = False) -> str:
    result = text
    for source, target in sorted(dictionary.items(), key=lambda item: -len(item[0])):
        result = result.replace(source, target)
    if result == text:
        # 未命中词典：保留行内代码/链接/公式等受保护片段，只包一层语言标记
        result = _decorate(text, label, prefix, heading)
    return result


def _mock_table(text: str, dictionary: dict[str, str], label: str, prefix: str) -> str:
    """表格必须保持列数/行数：只装饰单元格文本，绝不动 | 骨架。"""
    marker = f"{prefix}[{label}] " if prefix else f"[{label}] "
    lines: list[str] = []
    for index, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if not stripped or re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", stripped):
            lines.append(line)
            continue
        cells = line.split("|")
        decorated = []
        for cell_index, cell in enumerate(cells):
            # 首尾空片段是行首/行尾的 |
            if not cell.strip() or cell_index == 0 or cell_index == len(cells) - 1:
                decorated.append(cell)
                continue
            body = cell.strip()
            replaced = body
            for source, target in sorted(dictionary.items(), key=lambda item: -len(item[0])):
                replaced = replaced.replace(source, target)
            if replaced == body:
                replaced = marker + body
            decorated.append(f" {replaced} ")
        lines.append("|".join(decorated))
    return "\n".join(lines)


def _decorate(text: str, label: str, prefix: str, heading: bool) -> str:
    """把段落/标题/列表项转成带目标语言标记的等效文本，保持 Markdown 结构。"""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        marker = f"{prefix}[{label}] " if prefix else f"[{label}] "
        if heading:
            out.append(f"{line} {marker}".rstrip())
        elif re.match(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+", line):
            prefix_part = line[: len(line) - len(line.lstrip())]
            body = line.lstrip()
            m = re.match(r"^((?:[-*+]|\d{1,9}[.)])\s+)", body)
            assert m is not None
            out.append(prefix_part + m.group(1) + marker + body[m.end() :])
        elif stripped.startswith(">"):
            out.append(f"> {marker}{stripped.lstrip('> ').strip()}")
        else:
            out.append(marker + line)
    return "\n".join(out)


def _mock_ai_call(system_prompt: str, user_content: str) -> str:
    payload = extract_payload(user_content)
    language = detect_target_language(system_prompt)
    if "文件名翻译助手" in system_prompt:
        stem = payload.split("\n")[-1].replace("需要翻译的部分：", "").strip() or payload.strip()
        code = _MOCK_LANG_CODE.get(language, language[:2].upper())
        base_stem = re.sub(r"\s+", "_", stem.strip()) or "document"
        return f"{base_stem}_{code}" if code else base_stem
    if "术语抽取助手" in system_prompt:
        return ""
    return _mock_translate_block(payload, language)


class AIClient:
    """单例 AI 客户端。可被 monkeypatch 替换以方便测试。"""

    def __init__(self) -> None:
        self._cache = TranslationCache(2000, 7 * 24 * 3600)
        self._limiter: Optional[RateLimiter] = None
        self._limiter_size = 0
        self._session: Optional[requests.Session] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 内部
    def _ensure_session(self) -> requests.Session:
        if self._session is None:
            with self._lock:
                if self._session is None:
                    self._session = requests.Session()
        return self._session

    def _get_limiter(self, size: int) -> RateLimiter:
        with self._lock:
            if self._limiter is None or self._limiter_size != size:
                self._limiter = RateLimiter(size)
                self._limiter_size = size
            return self._limiter

    # ------------------------------------------------------------ 公共
    def is_mock(self) -> bool:
        return get_settings().is_mock()

    def ai_call(self, system_prompt: str, user_content: str) -> str:
        """翻译引擎注入点：mock 或真实调用，含缓存与重试。"""
        settings = get_settings()
        if settings.is_mock():
            return _mock_ai_call(system_prompt, user_content)

        key = TranslationCache.make_key(user_content, settings.model, system_prompt, "v4")
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._call_with_retry(system_prompt, user_content)
        self._cache.put(key, result)
        return result

    def _call_with_retry(self, system_prompt: str, user_content: str) -> str:
        settings = get_settings()
        limiter = self._get_limiter(settings.concurrency)
        attempts = max(0, settings.get("max_retries"))
        last_error: Optional[Exception] = None
        for attempt in range(attempts + 1):
            if attempt:
                delay = min(float(settings.get("retry_base_delay")) * (2 ** (attempt - 1)), 20.0)
                time.sleep(delay * (0.5 + random.random()))
            try:
                with limiter:
                    return self._call_once(system_prompt, user_content)
            except AIError as exc:
                last_error = exc
                if exc.kind in ("auth", "config"):
                    raise
                logger.warning("AI 调用失败（第 %d 次）：%s", attempt + 1, exc)
        raise AIError(f"AI 调用重试后仍失败: {last_error}", kind="network")

    def _call_once(self, system_prompt: str, user_content: str) -> str:
        settings = get_settings()
        if not settings.is_configured():
            raise AIError("AI 服务未配置", kind="config")
        headers = {"Authorization": settings.api_key, "Content-Type": "application/json"}
        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
        }
        session = self._ensure_session()
        started = time.perf_counter()
        try:
            resp = session.post(settings.api_url, headers=headers, json=payload, timeout=settings.get("timeout"))
        except requests.exceptions.Timeout as exc:
            raise AIError("AI 服务超时", kind="network") from exc
        except requests.exceptions.RequestException as exc:
            raise AIError(f"AI 服务连接失败: {exc}", kind="network") from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info(
            "AI 调用完成 model=%s status=%s duration_ms=%s",
            settings.model,
            resp.status_code,
            elapsed,
            extra={"event": "ai.call", "duration_ms": elapsed},
        )
        if resp.status_code in (401, 403):
            raise AIError("AI 鉴权失败，请检查密钥", kind="auth")
        if resp.status_code == 429:
            raise AIError("AI 服务限流", kind="rate_limit")
        if resp.status_code >= 500:
            raise AIError(f"AI 服务错误 {resp.status_code}", kind="network")
        try:
            resp.raise_for_status()
            data = resp.json()
            return str(data["choices"][0]["message"]["content"]).strip()
        except (ValueError, KeyError, IndexError, requests.exceptions.HTTPError) as exc:
            raise AIError(f"无法解析 AI 响应: {exc}", kind="parse") from exc

    def translate(
        self,
        content: str,
        target_language: str,
        prompt_template: str = "",
        log_callback: Callable[[str], None] | None = None,
    ) -> str:
        """兼容旧接口：单块直接翻译。"""
        if log_callback:
            log_callback(f"翻译 {target_language}（{len(content)} 字符）")
        return self.ai_call(
            prompt_template or "You are a translator. 翻译成 {target_language}".replace(
                "{target_language}", target_language
            ),
            content,
        )

    def test_connection(self) -> dict[str, object]:
        """配置中心的"测试连接"。"""
        settings = get_settings()
        if settings.is_mock():
            probe = _mock_ai_call("把文本翻译成 English", f"{CONTENT_BEGIN}Hello{CONTENT_END}")
            return {"ok": True, "mode": "mock", "latency_ms": 0, "message": f"离线 mock 可用（示例输出：{probe[:24]}）"}
        started = time.perf_counter()
        try:
            out = self._call_once(
                "You are a translation engine. Reply with the translated text only. 翻译成 English",
                f"{CONTENT_BEGIN}Hello{CONTENT_END}",
            )
        except AIError as exc:
            return {"ok": False, "mode": "live", "message": str(exc), "code": "E_AI_CONNECT"}
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": True, "mode": "live", "latency_ms": latency, "message": f"连接正常（输出：{out[:24]}）"}


ai_client = AIClient()
