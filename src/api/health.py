"""健康检查与运行时自检。"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from ..config import APP_VERSION
from ..core.settings_store import get_settings
from ..rendering.fonts import font_status
from ..task_manager import TaskState
from ._deps import manager

bp = Blueprint("health", __name__, url_prefix="/api")


def _pandoc_ok() -> bool:
    try:
        import pypandoc

        pypandoc.get_pandoc_version()
        return True
    except Exception:
        return False


def _weasyprint_ok() -> bool:
    try:
        import weasyprint  # noqa: F401

        return True
    except Exception:
        return False


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            return True
    except Exception:
        return False


@bp.get("/health")
def health():
    task_manager = manager()
    tasks = task_manager.list_tasks(limit=10_000)[0]
    running = sum(1 for task in tasks if task.state in (TaskState.RUNNING, TaskState.PAUSED))
    failed = sum(1 for task in tasks if task.state == TaskState.FAILURE)
    settings = get_settings()
    output_dir = task_manager._dir  # noqa: SLF001 - 内部只读属性
    checks = {
        "pandoc": _pandoc_ok(),
        "weasyprint": _weasyprint_ok(),
        "fonts": font_status()["count"],
        "data_dir_writable": _writable(output_dir.parent),
        "output_dir_writable": _writable(output_dir),
    }
    status = "ok" if all(value for key, value in checks.items() if key != "fonts") else "degraded"
    return jsonify(
        status=status,
        version=APP_VERSION,
        uptime_s=round(time.time() - current_app.extensions["m2p"]["started_at"], 1),
        checks=checks,
        ai={"configured": settings.is_configured(), "mock": settings.is_mock(), "mode": settings.public()["mode"], "model": settings.model},
        tasks={"total": len(tasks), "running": running, "failed": failed},
        pid=os.getpid(),
        disk_free_mb=_disk_free_mb(output_dir),
    )


def _disk_free_mb(path: Path) -> int:
    try:
        usage = shutil.disk_usage(path)
        return usage.free // 1024 // 1024
    except Exception:  # pragma: no cover
        return -1
