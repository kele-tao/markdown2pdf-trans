"""
AI 翻译客户端：
- 重试（指数退避 + jitter）
- 限流（信号量）
- 缓存命中直接返回
- 异常分两类：AIError（业务可重试/可降级） vs 其他（程序 bug）
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import requests

from .config import TRANSLATION_PROMPT, FILENAME_TRANSLATION_PROMPT, ai_config
from .concurrency import RateLimiter, retry_with_backoff
from .translation_cache import TranslationCache

logger = logging.getLogger(__name__)


class AIError(Exception):
    """可分类的 AI 调用错误，供上层决定如何重试/降级。"""

    def __init__(self, message: str, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind  # network | auth | rate_limit | parse | unknown


class AIClient:
    """单例 AI 客户端。可被 monkeypatch 替换以方便测试。"""

    def __init__(self) -> None:
        self._cache: Optional[TranslationCache] = None
        self._limiter: Optional[RateLimiter] = None
        self._session: Optional[requests.Session] = None
        self._lock = threading.Lock()

    def _ensure_init(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            from .config import CACHE_MAX_ENTRIES, CACHE_TTL_SECONDS

            self._cache = TranslationCache(CACHE_MAX_ENTRIES, CACHE_TTL_SECONDS)
            self._limiter = RateLimiter(ai_config.max_concurrency)
            self._session = requests.Session()

    @property
    def cache(self) -> TranslationCache:
        self._ensure_init()
        assert self._cache is not None
        return self._cache

    def translate(
        self,
        content: str,
        target_language: str,
        prompt_template: str = TRANSLATION_PROMPT,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        self._ensure_init()
        ai_config.require()

        # 缓存命中
        key = TranslationCache.make_key(content, target_language, prompt_template, ai_config.model)
        cached = self.cache.get(key)
        if cached is not None:
            if log_callback:
                log_callback("  -> [cache] hit")
            return cached

        # 真实调用
        if log_callback:
            log_callback(f"  -> [AI] Calling {ai_config.model} for {target_language}...")
        result = self._call_with_retry(content, target_language, prompt_template)
        self.cache.put(key, result)
        if log_callback:
            log_callback("  -> [AI] success")
        return result

    @retry_with_backoff(
        max_retries=ai_config.max_retries,
        base_delay=ai_config.retry_base_delay,
        retry_on=(AIError,),
    )
    def _call_with_retry(self, content: str, target_language: str, prompt_template: str) -> str:
        assert self._limiter is not None and self._session is not None
        with self._limiter:
            return self._call_once(content, target_language, prompt_template)

    def _call_once(self, content: str, target_language: str, prompt_template: str) -> str:
        assert self._session is not None
        headers = {
            "Authorization": ai_config.api_key,
            "Content-Type": "application/json",
        }
        prompt = prompt_template.format(target_language=target_language)
        payload = {
            "model": ai_config.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
        }
        try:
            resp = self._session.post(
                ai_config.api_url, headers=headers, json=payload, timeout=ai_config.timeout
            )
        except requests.exceptions.Timeout as e:
            raise AIError(f"AI 服务超时: {e}", kind="network") from e
        except requests.exceptions.ConnectionError as e:
            raise AIError(f"AI 服务连接失败: {e}", kind="network") from e
        except requests.exceptions.RequestException as e:
            raise AIError(f"AI 请求异常: {e}", kind="network") from e

        if resp.status_code in (401, 403):
            raise AIError("AI 鉴权失败，请检查 AI_API_KEY", kind="auth")
        if resp.status_code == 429:
            raise AIError("AI 服务限流", kind="rate_limit")
        if resp.status_code >= 500:
            raise AIError(f"AI 服务错误 {resp.status_code}", kind="network")
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise AIError(f"AI 返回错误状态: {e}", kind="unknown") from e

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError) as e:
            raise AIError(f"无法解析 AI 响应: {e}", kind="parse") from e


# 全局实例
ai_client = AIClient()
