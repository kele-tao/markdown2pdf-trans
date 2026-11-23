"""应用工厂与依赖装配。

- create_app() 负责：日志、配置中心、任务管理器、蓝图注册、错误处理
- 旧版 `python app.py` / `python -m src.app` 两种启动方式都支持
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .api import register_blueprints
from .config import (
    APP_VERSION,
    DATA_DIR,
    MAX_UPLOAD_BYTES,
    OUTPUT_DIR,
    STATIC_DIR,
    TEMPLATE_DIR,
    TASK_CLEANUP_INTERVAL,
    TASK_TTL_SECONDS,
)
from .core.errors import AppError
from .core.logging_setup import setup_logging
from .core.settings_store import configure_settings, get_settings
from .rendering.fonts import register_fontconfig
from .task_manager import TaskManager

logger = logging.getLogger("m2p.app")

_STARTED_AT = time.time()


def create_app(*, data_dir: Optional[Path] = None, start_cleanup: bool = True) -> Flask:
    setup_logging()
    base_data = Path(data_dir) if data_dir else DATA_DIR
    base_data.mkdir(parents=True, exist_ok=True)
    output_dir = OUTPUT_DIR if data_dir is None else base_data / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    if data_dir is not None:
        os.environ["M2P_OUTPUT_DIR"] = str(output_dir)

    configure_settings(base_data / "settings.json")
    register_fontconfig()

    app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    manager = TaskManager(
        base_output_dir=output_dir,
        ttl_seconds=TASK_TTL_SECONDS,
        cleanup_interval=TASK_CLEANUP_INTERVAL,
        registry_path=base_data / "tasks.json",
        persist=True,
    )
    if start_cleanup:
        manager.start_cleanup()

    app.extensions["m2p"] = {
        "task_manager": manager,
        "started_at": _STARTED_AT,
        "version": APP_VERSION,
    }

    register_blueprints(app)

    @app.route("/")
    def index():  # noqa: D401
        return render_template(
            "index.html",
            app_version=APP_VERSION,
            ai=get_settings().public(),
        )

    @app.route("/favicon.ico")
    def favicon():
        path = STATIC_DIR / "favicon.ico"
        if not path.exists():  # pragma: no cover - 兜底
            return ("", 204)
        return app.send_static_file("favicon.ico")

    @app.errorhandler(AppError)
    def handle_app_error(error: AppError):
        if request.path.startswith("/api/"):
            return jsonify(error.to_dict()), error.status
        return jsonify(error=error.message), error.status

    @app.errorhandler(413)
    def handle_too_large(_error):
        size_mb = MAX_UPLOAD_BYTES // 1024 // 1024
        return jsonify(error=f"文件过大，超过 {size_mb} MB 限制，请压缩或分批上传。", code="E_UPLOAD_TOO_LARGE"), 413

    @app.errorhandler(Exception)
    def handle_exception(error):
        if isinstance(error, HTTPException):
            return jsonify(error=f"HTTP {error.code}: {error.name}", code="E_HTTP"), error.code
        logger.exception("未处理异常: %s %s", request.method, request.path)
        return jsonify(error=f"服务器内部错误：{type(error).__name__}", code="E_INTERNAL"), 500

    @app.after_request
    def add_headers(response):
        if request.path.startswith("/api/") or request.path == "/":
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    logger.info("m2p 已就绪：版本 %s，数据目录 %s，输出目录 %s", APP_VERSION, base_data, output_dir)
    return app


def get_manager(app: Flask) -> TaskManager:
    return app.extensions["m2p"]["task_manager"]
