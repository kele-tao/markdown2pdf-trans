"""术语表：自动提取候选术语（AI 优先，离线启发式兜底）。"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..ai_client import ai_client
from ..core.errors import AppError, ErrorCode
from ..core.settings_store import get_settings
from ..file_utils import read_text_with_fallback
from ..translation import suggest_glossary
from ..uploads import UnsafePathError, safe_join
from ._deps import get_task

logger = logging.getLogger("m2p.api.glossary")
bp = Blueprint("glossary", __name__, url_prefix="/api")


@bp.post("/glossary/suggest")
def suggest():
    data = request.get_json(silent=True) or {}
    task_id = str(data.get("task_id") or "").strip()
    rel_path = str(data.get("file") or "").strip()
    language = str(data.get("target_language") or "English").strip()
    max_terms = int(data.get("max_terms") or 30)
    if not task_id or not rel_path:
        raise AppError("缺少 task_id 或 file", code=ErrorCode.BAD_REQUEST)
    task = get_task(task_id)
    try:
        source = safe_join(task.task_dir / "source", rel_path)
    except UnsafePathError as exc:
        raise AppError(f"路径不安全：{exc}", code=ErrorCode.PATH_UNSAFE) from exc
    if not source.is_file():
        raise AppError(f"文件不存在：{rel_path}", code=ErrorCode.NOT_FOUND, status=404)

    markdown = read_text_with_fallback(source)
    settings = get_settings()
    use_ai = not settings.is_mock()
    entries = suggest_glossary(
        markdown,
        language,
        ai_call=ai_client.ai_call if use_ai else None,
        max_terms=max(1, min(100, max_terms)),
    )
    terms = [
        {
            "source": entry.source,
            "target": entry.target,
            "confidence": 0.9 if entry.source != entry.target else 0.5,
        }
        for entry in entries
    ]
    return jsonify(terms=terms, mode="ai" if use_ai else "heuristic")
