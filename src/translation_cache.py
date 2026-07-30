"""
线程安全的 LRU + TTL 翻译缓存。
- LRU: 超过 max_entries 时淘汰最久未使用
- TTL: 单条超过 ttl_seconds 自动失效
- 缓存 key 由 (内容, 目标语言, prompt 模板, 模型) 决定，模型升级会自然失效旧条目
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Optional


class TranslationCache:
    def __init__(self, max_entries: int, ttl_seconds: int) -> None:
        self._max = max_entries
        self._ttl = ttl_seconds
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(content: str, target_language: str, prompt_template: str, model: str) -> str:
        h = hashlib.sha256()
        h.update(content.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
        h.update(target_language.encode("utf-8"))
        h.update(b"\x00")
        h.update(prompt_template.encode("utf-8"))
        h.update(b"\x00")
        h.update(model.encode("utf-8"))
        return h.hexdigest()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            ts, value = item
            if time.time() - ts > self._ttl:
                del self._data[key]
                self._misses += 1
                return None
            # 命中，移动到末尾表示最近使用
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (time.time(), value)
            # 淘汰最久未使用
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._data),
                "max": self._max,
                "hits": self._hits,
                "misses": self._misses,
            }

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = self._misses = 0
