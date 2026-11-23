"""元数据：主题、语言、导出模式、版本。"""
from __future__ import annotations

from flask import Blueprint, jsonify

from ..config import (
    APP_VERSION,
    BILINGUAL_LAYOUTS,
    EXPORT_MODES,
    FONT_STACKS,
    LANGUAGES,
    PAGE_SIZES,
    THEME_PRESETS,
    TONE_OPTIONS,
)
from ..rendering.fonts import font_status

bp = Blueprint("meta", __name__, url_prefix="/api")


@bp.get("/themes")
def themes():
    return jsonify(themes=THEME_PRESETS, fonts=FONT_STACKS)


@bp.get("/languages")
def languages():
    from ..config import LANGUAGE_SPECS

    return jsonify(languages=LANGUAGE_SPECS)


@bp.get("/meta")
def meta():
    from ..config import LANGUAGE_SPECS, MAX_UPLOAD_MB, TASK_TTL_SECONDS

    return jsonify(
        version=APP_VERSION,
        languages=LANGUAGE_SPECS,
        export_modes=[{"code": code, "label": label} for code, label in EXPORT_MODES],
        bilingual_layouts=[{"code": code, "label": label} for code, label in BILINGUAL_LAYOUTS],
        tones=[{"code": code, "label": label} for code, label in TONE_OPTIONS],
        themes=THEME_PRESETS,
        fonts=FONT_STACKS,
        page_sizes=PAGE_SIZES,
        limits={"max_upload_mb": MAX_UPLOAD_MB, "task_ttl_hours": TASK_TTL_SECONDS // 3600},
        font_status=font_status(),
    )
