"""旧版根路径接口兼容层。

保证 v3 的前端/脚本/测试仍然可用；内部全部委托到 /api 蓝图实现，
避免两套业务逻辑漂移。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..core.errors import AppError
from ..core.settings_store import get_settings
from . import preview as preview_api
from . import tasks as tasks_api
from . import uploads as uploads_api

bp = Blueprint("legacy", __name__)


@bp.get("/ai_status")
def ai_status():
    settings = get_settings()
    return jsonify(
        configured=settings.is_configured(),
        model=settings.model or None,
        mode=settings.public()["mode"],
        mock=settings.is_mock(),
    )


@bp.post("/prepare_upload")
def prepare_upload():
    return uploads_api.prepare_upload_legacy()


@bp.post("/start_conversion")
def start_conversion():
    """旧接口语义：启动成功返回 200（新接口 /api/tasks/<id>/start 返回 202）。"""
    response = tasks_api.legacy_start()
    if isinstance(response, tuple):
        payload, _status = response
        return payload, 200
    return response


@bp.post("/<action>/<task_id>")
def control(action: str, task_id: str):
    """旧接口语义：控制信号总是 200（新接口对非法状态返回 409）。"""
    try:
        return tasks_api.legacy_control(action, task_id)
    except AppError as exc:
        if exc.status == 409:
            return jsonify(message=f"Task {action} signal ignored (state unchanged)", ok=False)
        raise


@bp.get("/status/<task_id>")
def status(task_id: str):
    return tasks_api.legacy_status(task_id)


@bp.get("/download/<task_id>")
def download(task_id: str):
    return tasks_api.legacy_download(task_id)


@bp.post("/preview/original")
def preview_original():
    return preview_api.legacy_preview_original()


@bp.post("/preview/translated")
def preview_translated():
    return preview_api.legacy_preview_translated()
