"""结构化日志。

默认输出人类可读行；设置 M2P_LOG_FORMAT=json 时输出 JSON Lines，便于容器采集。
所有日志通过 logging 模块，禁止把密钥写进日志（settings 层负责脱敏）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("task_id", "file", "event", "duration_ms", "code", "blocks", "language"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = os.environ.get("M2P_LOG_FORMAT", "text").strip().lower()
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    _CONFIGURED = True


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    """带结构化字段的日志（字段会进入 JSON 日志）。"""
    logger.log(level, message, extra={key: fields[key] for key in fields if fields[key] is not None})


class Stopwatch:
    """耗时统计上下文：with Stopwatch() as sw: ...; sw.ms"""

    def __enter__(self) -> "Stopwatch":
        self._start = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._start) * 1000)
