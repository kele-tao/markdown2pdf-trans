"""配置中心存储。

优先级：运行期覆盖（data/settings.json） > 环境变量 > 内置默认值。
密钥永不回显明文，也不写日志；落盘时用最简混淆（base64 前缀 enc:）并限制文件权限。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "api_url": "https://api.openai.com/v1/chat/completions",
    "api_key": "",
    "model": "gpt-4o-mini",
    "concurrency": 3,
    "timeout": 180,
    "max_retries": 3,
    "retry_base_delay": 1.5,
    "mock_enabled": True,
}

_ENV_MAP = {
    "api_url": "AI_API_URL",
    "api_key": "AI_API_KEY",
    "model": "AI_MODEL",
    "concurrency": "AI_MAX_CONCURRENCY",
    "timeout": "AI_TIMEOUT",
    "max_retries": "AI_MAX_RETRIES",
    "retry_base_delay": "AI_RETRY_BASE_DELAY",
    "mock_enabled": "M2P_AI_MOCK",
}

_INT_FIELDS = {"concurrency", "timeout", "max_retries"}
_FLOAT_FIELDS = {"retry_base_delay"}
_BOOL_FIELDS = {"mock_enabled"}


def _to_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return "enc:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not value:
        return ""
    if value.startswith("enc:"):
        try:
            return base64.b64decode(value[4:]).decode("utf-8")
        except Exception:  # pragma: no cover - 损坏数据回退为空
            logger.warning("settings: 无法解密存储的密钥，已忽略")
            return ""
    return value


def mask_key(key: str) -> str:
    """掩码展示：只暴露前 3 位与后 4 位，长度过短则全掩码。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}…{key[-4:]}"


class SettingsStore:
    """线程安全的运行期配置存储。"""

    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = path
        self._overrides: dict[str, Any] = {}
        if path is not None:
            self._load()

    # ---------------------------------------------------------------- 读写
    def _load(self) -> None:
        assert self._path is not None
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - 损坏文件不应阻止启动
            logger.warning("settings: 读取 %s 失败（%s），使用默认配置", self._path, exc)
            return
        if isinstance(raw, dict):
            self._overrides = raw
            logger.info("settings: 已加载运行期配置 %s", self._path)

    def _save(self) -> None:
        assert self._path is not None
        data = dict(self._overrides)
        if data.get("api_key"):
            data["api_key"] = _encrypt(str(data["api_key"]))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        try:  # 尽力收紧权限（POSIX；Windows 忽略）
            os.chmod(self._path, 0o600)
        except OSError:  # pragma: no cover
            pass

    # ---------------------------------------------------------------- 取值
    def _raw(self, field: str) -> Any:
        with self._lock:
            if field in self._overrides:
                value = self._overrides[field]
                if field == "api_key":
                    return _decrypt(str(value))
                return value
        env_key = _ENV_MAP.get(field)
        if env_key:
            env_value = os.environ.get(env_key, "").strip()
            if env_value:
                return env_value
        return DEFAULTS.get(field)

    def get(self, field: str) -> Any:
        value = self._raw(field)
        if field in _INT_FIELDS:
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(DEFAULTS[field])
        if field in _FLOAT_FIELDS:
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(DEFAULTS[field])
        if field in _BOOL_FIELDS:
            return _to_bool(value, bool(DEFAULTS[field]))
        return "" if value is None else str(value)

    @property
    def api_url(self) -> str:
        return self.get("api_url")

    @property
    def api_key(self) -> str:
        return self.get("api_key")

    @property
    def model(self) -> str:
        return self.get("model")

    @property
    def concurrency(self) -> int:
        return max(1, min(16, self.get("concurrency")))

    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key and self.model)

    def is_mock(self) -> bool:
        """未配置密钥时自动降级为 mock；显式开启 mock 也走 mock。"""
        if self.get("mock_enabled"):
            return True
        return not self.is_configured()

    def source(self, field: str = "api_key") -> str:
        with self._lock:
            if field in self._overrides:
                return "runtime"
        env_key = _ENV_MAP.get(field)
        if env_key and os.environ.get(env_key, "").strip():
            return "env"
        return "default"

    # ---------------------------------------------------------------- 更新
    def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验并写入更新；空字符串/None 表示不修改。"""
        changed: dict[str, Any] = {}
        with self._lock:
            if payload.get("clear_key"):
                self._overrides["api_key"] = ""
                changed["api_key"] = ""
            for field in ("api_url", "model", "api_key"):
                if field in payload and not payload.get("clear_key"):
                    value = str(payload.get(field) or "").strip()
                    if value:
                        self._overrides[field] = value
                        changed[field] = value
            if "concurrency" in payload and payload["concurrency"] is not None:
                value = max(1, min(16, int(payload["concurrency"])))
                self._overrides["concurrency"] = value
                changed["concurrency"] = value
            if "timeout" in payload and payload["timeout"] is not None:
                value = max(10, min(600, int(payload["timeout"])))
                self._overrides["timeout"] = value
                changed["timeout"] = value
            if "max_retries" in payload and payload["max_retries"] is not None:
                value = max(0, min(10, int(payload["max_retries"])))
                self._overrides["max_retries"] = value
                changed["max_retries"] = value
            if "mock_enabled" in payload and payload["mock_enabled"] is not None:
                value = _to_bool(payload["mock_enabled"])
                self._overrides["mock_enabled"] = value
                changed["mock_enabled"] = value
            if changed:
                self._save()
        if changed:
            logger.info("settings: 已更新字段 %s", sorted(changed))
        return changed

    def public(self) -> dict[str, Any]:
        """可安全回传前端的视图：密钥只给掩码与是否已设置。"""
        key = self.api_key
        return {
            "api_url": self.api_url,
            "model": self.model,
            "key_set": bool(key),
            "api_key_masked": mask_key(key),
            "concurrency": self.concurrency,
            "timeout": self.get("timeout"),
            "max_retries": self.get("max_retries"),
            "mock_enabled": self.is_mock(),
            "source": self.source("api_key"),
            "mode": "mock" if self.is_mock() else "live",
        }


_store: SettingsStore | None = None


def configure_settings(path: Path | None) -> SettingsStore:
    global _store
    _store = SettingsStore(path)
    return _store


def get_settings() -> SettingsStore:
    global _store
    if _store is None:
        _store = SettingsStore(None)
    return _store
