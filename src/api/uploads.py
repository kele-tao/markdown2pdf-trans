"""上传接口：单文件 / 文件夹 / ZIP。"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..config import MAX_UPLOAD_BYTES
from ..core.errors import AppError, ErrorCode
from ..task_manager import TaskState
from ..uploads import (
    UnsafePathError,
    collect_markdown_files,
    save_folder_files,
    save_single_file,
    tree_size,
    unzip_safely,
)
from ._deps import manager

logger = logging.getLogger("m2p.api.uploads")
bp = Blueprint("uploads", __name__, url_prefix="/api")


def _file_list(source_dir: Path) -> list[dict]:
    items = []
    for rel in collect_markdown_files(source_dir):
        full = source_dir / rel
        try:
            size = full.stat().st_size
        except OSError:
            size = 0
        items.append({"path": rel, "size": size})
    return items


@bp.post("/uploads")
def upload():
    upload_type = (request.form.get("type") or request.form.get("upload_type") or "").strip().lower()
    if upload_type not in ("zip", "folder", "file"):
        raise AppError("请指定上传类型（zip / folder / file）", code=ErrorCode.UPLOAD_BAD_TYPE)

    task = manager().create()
    source_dir = task.task_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    try:
        if upload_type == "folder":
            files = request.files.getlist("files") or request.files.getlist("files[]")
            if not files:
                raise AppError("未选择文件夹内容", code=ErrorCode.UPLOAD_NO_FILE)
            save_folder_files(files, source_dir, max_total_bytes=MAX_UPLOAD_BYTES)
        elif upload_type == "file":
            storage = request.files.get("file") or request.files.get("files")
            if not storage or not storage.filename:
                raise AppError("未选择文件", code=ErrorCode.UPLOAD_NO_FILE)
            if not storage.filename.lower().endswith(".md"):
                raise AppError(f"请上传 .md 文件（收到：{storage.filename}）", code=ErrorCode.UPLOAD_BAD_TYPE)
            save_single_file(storage, source_dir, ".md")
        else:
            storage = request.files.get("zipfile") or request.files.get("file") or request.files.get("files")
            if not storage or not storage.filename:
                raise AppError("未选择文件", code=ErrorCode.UPLOAD_NO_FILE)
            if not storage.filename.lower().endswith(".zip"):
                raise AppError(f"请上传 .zip 文件（收到：{storage.filename}）", code=ErrorCode.UPLOAD_BAD_TYPE)
            head = b""
            try:
                head = storage.stream.read(2)
                storage.stream.seek(0)
            except Exception:  # pragma: no cover
                pass
            if head != b"PK":
                raise AppError("文件不是有效的 ZIP 格式", code=ErrorCode.UPLOAD_BAD_ZIP)
            zip_path = task.task_dir / "source.zip"
            storage.save(zip_path)
            try:
                _saved, skipped = unzip_safely(zip_path, source_dir)
            except zipfile.BadZipFile as exc:
                raise AppError("ZIP 文件已损坏，无法解压", code=ErrorCode.UPLOAD_BAD_ZIP) from exc
            except ValueError as exc:
                raise AppError(str(exc), code=ErrorCode.UPLOAD_TOO_LARGE, status=413) from exc
            if skipped:
                logger.warning("ZIP 跳过 %d 个不安全条目：%s", len(skipped), skipped[:5])

        files = _file_list(source_dir)
        if not files:
            raise AppError("未找到可处理的 .md 文件", code=ErrorCode.UPLOAD_EMPTY)
        if tree_size(source_dir) > MAX_UPLOAD_BYTES:
            raise AppError("解压后体积超过限制", code=ErrorCode.UPLOAD_TOO_LARGE, status=413)

        task.preview_files = [item["path"] for item in files]
        task.title = Path(files[0]["path"]).stem
        task.set_state(TaskState.READY)
        return jsonify(task_id=task.id, files=files, task=task.summary())
    except UnsafePathError as exc:
        manager().delete(task.id)
        raise AppError(f"路径不安全：{exc}", code=ErrorCode.PATH_UNSAFE) from exc
    except AppError:
        manager().delete(task.id)
        raise
    except ValueError as exc:
        manager().delete(task.id)
        raise AppError(str(exc), code=ErrorCode.UPLOAD_BAD_TYPE) from exc
    except Exception as exc:  # noqa: BLE001
        manager().delete(task.id)
        logger.exception("上传处理失败")
        raise AppError(f"上传处理失败：{exc}", code=ErrorCode.INTERNAL, status=500) from exc


# ---------------------------------------------------------------- 兼容旧接口
@bp.post("/prepare_upload")
def prepare_upload_legacy():
    """旧版接口：字段 upload_type + file/zipfile/files[]，返回 {task_id, preview_files}。"""
    response = upload()
    payload = response.get_json()
    return jsonify(task_id=payload["task_id"], preview_files=[item["path"] for item in payload["files"]])
