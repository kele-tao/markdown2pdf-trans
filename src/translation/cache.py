"""翻译块级缓存。

key = 块内容 + 目标语言 + 命中术语 + 文体 + 提示词版本 + 命名空间。
线程安全的 LRU + TTL，只缓存通过结构校验的译文。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from typing import Optional

from .models import GlossaryEntry


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class ChunkCache:
    """线程安全的块级缓存（LRU + TTL）。"""

    def __init__(self, max_entries: int = 4000, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self._max = max(1, int(max_entries))
        self._ttl = max(0, int(ttl_seconds))
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            timestamp, value = item
            if self._ttl and time.time() - timestamp > self._ttl:
                del self._data[key]
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (time.time(), value)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "max": self._max,
                "hits": self._hits,
                "misses": self._misses,
            }


def make_cache_key(
    text: str,
    target_language: str,
    glossary: list[GlossaryEntry],
    tone: str,
    prompt_version: str,
    namespace: str = "",
) -> str:
    digest = hashlib.sha256()
    parts = [
        text,
        target_language.strip().casefold(),
        tone.strip().casefold(),
        prompt_version,
        namespace,
    ]
    for part in parts:
        digest.update(part.encode("utf-8", errors="ignore"))
        digest.update(b"\x00")
    for entry in sorted(glossary, key=lambda item: item.source.casefold()):
        digest.update(entry.source.casefold().encode("utf-8", errors="ignore"))
        digest.update(b"\x1f")
        digest.update(entry.target.encode("utf-8", errors="ignore"))
        digest.update(b"\x00")
    return digest.hexdigest()


_DEFAULT_CACHE = ChunkCache(
    max_entries=_env_int("M2P_TRANSLATION_CACHE_MAX", 4000),
    ttl_seconds=_env_int("M2P_TRANSLATION_CACHE_TTL", 7 * 24 * 3600),
)


def get_default_cache() -> ChunkCache:
    return _DEFAULT_CACHE


def clear_translation_cache() -> None:
    """清空进程内共享的块级翻译缓存（测试与运维可用）。"""
    _DEFAULT_CACHE.clear()
