"""蓝图共享依赖：任务管理器、预览渲染、错误包装。"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from flask import current_app

from ..core.errors import AppError, ErrorCode
from ..task_manager import Task, TaskManager

T = TypeVar("T")


def manager() -> TaskManager:
    return current_app.extensions["m2p"]["task_manager"]


def get_task(task_id: str) -> Task:
    task = manager().get(task_id)
    if task is None:
        raise AppError("任务不存在或已被清理", code=ErrorCode.TASK_NOT_FOUND, status=404)
    return task


def require_json() -> dict[str, Any]:
    from flask import request

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise AppError("请求体必须是 JSON 对象", code=ErrorCode.BAD_REQUEST)
    return data


def guard(fn: Callable[[], T]) -> T:
    """把已知异常翻译成 AppError（供蓝图内部复用）。"""
    try:
        return fn()
    except AppError:
        raise
    except FileNotFoundError as exc:
        raise AppError(f"文件不存在：{exc}", code=ErrorCode.NOT_FOUND, status=404) from exc
