"""向后兼容：旧的 `from .conversion import run_conversion` 调用点。"""
from __future__ import annotations

from .pipeline.conversion import run_conversion, start_conversion_thread  # noqa: F401

__all__ = ["run_conversion", "start_conversion_thread"]
