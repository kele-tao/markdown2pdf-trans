"""HTTP API 蓝图集合。"""
from __future__ import annotations

from flask import Flask

from .glossary import bp as glossary_bp
from .health import bp as health_bp
from .legacy import bp as legacy_bp
from .meta import bp as meta_bp
from .preview import bp as preview_bp
from .settings import bp as settings_bp
from .tasks import bp as tasks_bp
from .uploads import bp as uploads_bp


def register_blueprints(app: Flask) -> None:
    for blueprint in (
        health_bp,
        settings_bp,
        meta_bp,
        uploads_bp,
        tasks_bp,
        preview_bp,
        glossary_bp,
        legacy_bp,
    ):
        app.register_blueprint(blueprint)
