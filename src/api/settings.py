"""配置中心：读写 AI 配置、测试连通性。密钥只回显掩码。"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from ..ai_client import ai_client
from ..config import (
    APP_VERSION,
    BILINGUAL_LAYOUTS,
    EXPORT_MODES,
    LANGUAGES,
    MAX_IMAGE_BYTES,
    MAX_UPLOAD_MB,
    PAGE_SIZES,
    THEME_PRESETS,
    TASK_TTL_SECONDS,
    TONE_OPTIONS,
)
from ..core.settings_store import get_settings
from ._deps import require_json

logger = logging.getLogger("m2p.api.settings")
bp = Blueprint("settings", __name__, url_prefix="/api")


def _settings_payload() -> dict:
    settings = get_settings()
    return {
        "ai": settings.public(),
        "limits": {
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_image_mb": MAX_IMAGE_BYTES // 1024 // 1024,
            "task_ttl_hours": TASK_TTL_SECONDS // 3600,
        },
        "languages": [{"code": code, "label": label} for code, label in LANGUAGES],
        "export_modes": [{"code": code, "label": label} for code, label in EXPORT_MODES],
        "bilingual_layouts": [{"code": code, "label": label} for code, label in BILINGUAL_LAYOUTS],
        "tones": [{"code": code, "label": label} for code, label in TONE_OPTIONS],
        "themes": THEME_PRESETS,
        "page_sizes": PAGE_SIZES,
        "version": APP_VERSION,
    }


@bp.get("/settings")
def get_settings_api():
    return jsonify(_settings_payload())


@bp.put("/settings")
def update_settings():
    payload = require_json()
    get_settings().update(payload)
    return jsonify(ok=True, **_settings_payload())


@bp.post("/settings/test")
def test_settings():
    payload = require_json() if _has_json() else {}
    settings = get_settings()
    if payload.get("api_url") or payload.get("api_key") or payload.get("model"):
        # 使用临时覆盖测试，不写盘
        snapshot = dict(settings._overrides)  # noqa: SLF001
        try:
            settings.update(payload)
            result = ai_client.test_connection()
        finally:
            settings._overrides = snapshot  # noqa: SLF001
        status = 200 if result.get("ok") else 502
        return jsonify(result), status
    result = ai_client.test_connection()
    status = 200 if result.get("ok") else 502
    return jsonify(result), status


def _has_json() -> bool:
    from flask import request

    return request.is_json and bool(request.get_json(silent=True))
