"""任务接口：列表 / 详情 / 状态 / 启动 / 控制 / 重试 / 下载 / 删除。"""
from __future__ import annotations

import logging
import os
import threading
import time
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, send_from_directory

from ..core.errors import AppError, ErrorCode
from ..core.settings_store import get_settings
from ..pipeline.conversion import start_conversion_thread
from ..pipeline.options import InvalidOptions, normalize_start_options
from ..task_manager import TaskState
from ._deps import get_task, manager, require_json

logger = logging.getLogger("m2p.api.tasks")
bp = Blueprint("tasks", __name__, url_prefix="/api")

_BUSY_STATES = (TaskState.RUNNING, TaskState.PAUSED, TaskState.PREPARING, TaskState.STOPPING)


def _ensure_settled(task) -> None:
    if task.state in _BUSY_STATES:
        raise AppError(f"任务当前状态为 {task.state.value}，请稍后再试", code=ErrorCode.TASK_BUSY, status=409)


def _download_name(task) -> str:
    return f"Translated_Results_{task.id[:8]}.zip"


# ---------------------------------------------------------------- 查询
@bp.get("/tasks")
def list_tasks():
    limit = max(1, min(100, int(request.args.get("limit", 20))))
    offset = max(0, int(request.args.get("offset", 0)))
    state = (request.args.get("state") or "").strip()
    query = (request.args.get("q") or "").strip()
    items, total = manager().list_tasks(limit=limit, offset=offset, state=state, query=query)
    return jsonify(items=[task.summary() for task in items], total=total, limit=limit, offset=offset)


@bp.get("/tasks/<task_id>")
def task_detail(task_id: str):
    task = get_task(task_id)
    return jsonify(task=task.detail())


@bp.get("/tasks/<task_id>/status")
def task_status(task_id: str):
    task = get_task(task_id)
    counts = task.counts()
    return jsonify(
        state=task.state.value,
        progress=task.progress,
        current_file=task.current_file,
        logs=task.drain_logs(),
        error=task.error,
        result_url=task.result_url,
        counts=counts,
        files=[entry.to_dict() for entry in task.files],
        updated_at=task.updated_at,
    )


@bp.get("/status/<task_id>")
def legacy_status(task_id: str):
    """旧版轮询接口：logs 为 [{"log": "..."}]。"""
    task = get_task(task_id)
    return jsonify(
        state=task.state.value,
        progress=task.progress,
        current_file=task.current_file,
        logs=[{"log": entry["message"]} for entry in task.drain_logs()],
        error=task.error,
        result_url=task.result_url,
    )


# ---------------------------------------------------------------- 启动 / 控制
@bp.post("/tasks/<task_id>/start")
def start_task(task_id: str):
    task = get_task(task_id)
    _ensure_settled(task)
    payload = request.get_json(silent=True) or {}
    try:
        options = normalize_start_options(payload)
    except InvalidOptions as exc:
        raise AppError(str(exc), code=ErrorCode.BAD_REQUEST) from exc

    settings = get_settings()
    if options["export_mode"] != "original" and not settings.is_configured() and not settings.is_mock():
        raise AppError(
            "AI 服务未配置：请在配置中心填写地址/密钥/模型，或启用离线演示模式",
            code=ErrorCode.AI_UNCONFIGURED,
        )
    task.reset_for_retry()
    task.options = {**options, "mode": "mock" if settings.is_mock() else "live"}
    start_conversion_thread(task, options)
    return jsonify(task_id=task.id, message="已启动"), 202


@bp.post("/tasks/<task_id>/pause")
def pause_task(task_id: str):
    task = get_task(task_id)
    if not task.request_pause():
        raise AppError(f"当前状态 {task.state.value} 不可暂停", code=ErrorCode.TASK_STATE, status=409)
    return jsonify(ok=True, state=task.state.value)


@bp.post("/tasks/<task_id>/resume")
def resume_task(task_id: str):
    task = get_task(task_id)
    if not task.request_resume():
        raise AppError(f"当前状态 {task.state.value} 不可继续", code=ErrorCode.TASK_STATE, status=409)
    return jsonify(ok=True, state=task.state.value)


@bp.post("/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    task = get_task(task_id)
    if not task.request_stop():
        raise AppError(f"当前状态 {task.state.value} 不可取消", code=ErrorCode.TASK_STATE, status=409)
    return jsonify(ok=True, state=task.state.value)


@bp.post("/tasks/<task_id>/retry")
def retry_task(task_id: str):
    task = get_task(task_id)
    _ensure_settled(task)
    payload = request.get_json(silent=True) or {}
    target = str(payload.get("file") or "").strip()
    failed = [entry.path for entry in task.files if entry.state == "failed"]
    if target:
        if target not in {entry.path for entry in task.files}:
            raise AppError(f"文件不存在于该任务：{target}", code=ErrorCode.NOT_FOUND, status=404)
        retried = [target]
    else:
        if not failed:
            raise AppError("没有失败文件需要重试", code=ErrorCode.TASK_STATE, status=409)
        retried = failed

    for entry in task.files:
        if entry.path in retried:
            entry.state = "pending"
            entry.error = None
            entry.retryable = False
            entry.warnings = []
            entry.elapsed_ms = 0
    task.error = None
    task.result_url = None
    task.progress = 0
    task.state = TaskState.READY
    task.stop_event.clear()
    task.pause_event.set()
    task.append_log(f"重试 {len(retried)} 个文件：{'、'.join(retried[:5])}", "warn")
    options = dict(task.options)
    if not options:
        raise AppError("任务缺少配置，无法重试，请重新开始任务", code=ErrorCode.TASK_STATE, status=409)
    start_conversion_thread(task, options, retry_files=retried)
    return jsonify(task_id=task.id, retried=retried), 202


# ---------------------------------------------------------------- 兼容旧控制接口
@bp.post("/<action>/<task_id>")
def legacy_control(action: str, task_id: str):
    mapping = {"pause": pause_task, "resume": resume_task, "stop": cancel_task}
    handler = mapping.get(action)
    if handler is None:
        raise AppError("无效操作", code=ErrorCode.NOT_FOUND, status=404)
    return handler(task_id)


@bp.post("/start_conversion")
def legacy_start():
    data = request.get_json(silent=True) or {}
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        raise AppError("缺少 task_id", code=ErrorCode.BAD_REQUEST)
    payload = dict(data)
    payload.pop("task_id", None)
    payload.setdefault("style_options", data.get("style_options") or {})
    response = start_task(task_id)
    if isinstance(response, tuple):  # 202
        _, status = response
        return jsonify(task_id=task_id, message="Process started."), status
    return response


# ---------------------------------------------------------------- 下载 / 删除
@bp.get("/tasks/<task_id>/download")
def download_task(task_id: str):
    task = get_task(task_id)
    zip_path = task.task_dir / _download_name(task)
    if task.state != TaskState.SUCCESS and not zip_path.is_file():
        raise AppError(f"任务未完成，当前状态：{task.state.value}", code=ErrorCode.TASK_STATE)
    if not zip_path.is_file():
        raise AppError("结果文件不存在或已被清理", code=ErrorCode.NOT_FOUND, status=404)
    return send_from_directory(str(task.task_dir), zip_path.name, as_attachment=True)


@bp.get("/download/<task_id>")
def legacy_download(task_id: str):
    return download_task(task_id)


@bp.delete("/tasks/<task_id>")
def delete_task(task_id: str):
    task = get_task(task_id)
    if task.state in (TaskState.RUNNING, TaskState.PAUSED, TaskState.PREPARING, TaskState.STOPPING):
        raise AppError("任务正在运行，无法删除，请先取消", code=ErrorCode.TASK_RUNNING, status=409)
    manager().delete(task_id)
    return jsonify(ok=True, deleted=task_id)
