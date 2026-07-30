"""
Flask 应用入口（重构版）。
- 配置 / 翻译 / 渲染 / 任务管理全部在独立模块
- 此文件只负责路由 + 异常处理
- 启动: `python -m src.app`
"""
from __future__ import annotations

import logging
import os
import re
import threading
import traceback
import zipfile

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from werkzeug.exceptions import HTTPException

from .ai_client import AIError, ai_client
from .config import (
    EXPORT_MODES,
    LANGUAGES,
    MAX_UPLOAD_BYTES,
    OUTPUT_DIR,
    STATIC_DIR,
    TEMPLATE_DIR,
    THEME_PRESETS,
    ai_config,
)
from .conversion import run_conversion
from .file_utils import get_pdf_page_count, read_text_with_fallback
from .renderer import RenderError, html_to_pdf_bytes, md_to_html
from .task_manager import TaskManager, TaskState
from .uploads import (
    UnsafePathError,
    collect_markdown_files,
    save_folder_files,
    save_single_file,
    safe_join,
    unzip_safely,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# 抑制 werkzeug 的 "development server" 警告（启动横幅已有更明确的提示）
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logger = logging.getLogger("m2p")

# -----------------------------------------------------------------------------
# Flask app
# -----------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

# 单例 TaskManager；cleanup 线程由 import 触发，符合"长跑"特性。
# 测试场景下用 monkeypatch 替换 start_cleanup / 类即可。
task_manager = TaskManager(
    base_output_dir=OUTPUT_DIR,
    ttl_seconds=int(os.environ.get("M2P_TASK_TTL_SECONDS", "86400")),
    cleanup_interval=int(os.environ.get("M2P_TASK_CLEANUP_INTERVAL", "1800")),
)
task_manager.start_cleanup()


# -----------------------------------------------------------------------------
# 错误处理
# -----------------------------------------------------------------------------
@app.errorhandler(413)
def handle_too_large(e):
    return jsonify(
        error=f"文件过大，超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 限制。请压缩或减少文件数。"
    ), 413


@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify(error=f"HTTP {e.code}: {e.name}"), e.code
    traceback.print_exc()
    return jsonify(error=f"服务器内部错误: {type(e).__name__}: {e}"), 500


# -----------------------------------------------------------------------------
# 页面
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    import json
    return render_template(
        "index.html",
        languages=LANGUAGES,
        export_modes=EXPORT_MODES,
        themes=THEME_PRESETS,
        themes_json=json.dumps(THEME_PRESETS, ensure_ascii=False),
    )


@app.route("/favicon.ico")
def favicon():
    p = STATIC_DIR / "favicon.ico"
    if not p.exists():
        # 最小 16x16 紫色方块 ICO（struct 拼，绝不出错）
        import struct
        ico = struct.pack("<HHH", 0, 1, 1)
        ico += struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, 40 + 16*16*4 + 16*16//8, 22)
        ico += struct.pack("<IiiHHIIiiII", 40, 16, 32, 1, 32, 0, 16*16*4, 0, 0, 0, 0)
        ico += (b"\x63\x66\xf1\xff") * (16 * 16)
        ico += b"\x00" * (16 * 16 // 8)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(ico)
    return send_file(str(p), mimetype="image/vnd.microsoft.icon")


# -----------------------------------------------------------------------------
# AI 状态
# -----------------------------------------------------------------------------
@app.route("/ai_status")
def ai_status():
    model = os.environ.get("AI_MODEL", "").strip() or ai_config.model or None
    return jsonify({
        "configured": ai_config.is_configured(),
        "model": model,
    })


# -----------------------------------------------------------------------------
# 上传
# -----------------------------------------------------------------------------
@app.route("/prepare_upload", methods=["POST"])
def prepare_upload():
    upload_type = request.form.get("upload_type")
    task = task_manager.create()
    source_dir = task.task_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    preview_files: list[str] = []

    try:
        if upload_type == "folder":
            files = request.files.getlist("files[]")
            if not files:
                return jsonify(error="未选择文件夹内容"), 400
            save_folder_files(files, source_dir, max_total_bytes=MAX_UPLOAD_BYTES)
        elif upload_type == "file":
            f = request.files.get("file")
            if not f or not f.filename:
                return jsonify(error="未选择文件"), 400
            if not f.filename.lower().endswith(".md"):
                return jsonify(error=f"请上传 .md 文件（收到: {f.filename}）"), 400
            name = save_single_file(f, source_dir, ".md")
            preview_files = [name]
        else:  # zip
            f = request.files.get("zipfile")
            if not f or not f.filename:
                return jsonify(error="未选择文件"), 400
            if not f.filename.lower().endswith(".zip"):
                return jsonify(error=f"请上传 .zip 文件（收到: {f.filename}）"), 400
            # 先读前 4 字节判断是不是真 zip
            head = f.stream.read(4) if hasattr(f, "stream") else b""
            try:
                f.stream.seek(0)
            except Exception:
                pass
            if head[:2] != b"PK":
                return jsonify(error="文件不是有效的 ZIP 格式"), 400
            zip_path = task.task_dir / "source.zip"
            f.save(zip_path)
            try:
                _saved, skipped = unzip_safely(zip_path, source_dir)
            except zipfile.BadZipFile:
                task_manager.delete(task.id)
                return jsonify(error="ZIP 文件已损坏，无法解压"), 400
            if skipped:
                # 把安全跳过的条目告诉用户（不算致命错误）
                logger.warning("ZIP 跳过了 %d 个不安全条目: %s", len(skipped), skipped[:5])

        if not preview_files:
            preview_files = collect_markdown_files(source_dir)

        if not preview_files:
            task_manager.delete(task.id)
            return jsonify(error="未找到可处理的 .md 文件"), 400

        task.set_state(TaskState.READY)
        task.preview_files = preview_files
        return jsonify(task_id=task.id, preview_files=preview_files)

    except UnsafePathError as e:
        task_manager.delete(task.id)
        return jsonify(error=f"路径不安全: {e}"), 400
    except ValueError as e:
        task_manager.delete(task.id)
        return jsonify(error=str(e)), 400
    except Exception as e:
        traceback.print_exc()
        task_manager.delete(task.id)
        return jsonify(error=f"上传处理失败: {e}"), 500


# -----------------------------------------------------------------------------
# 预览
# -----------------------------------------------------------------------------
def _do_preview(task_id: str, preview_file: str, style_options: dict, content_modifier=None) -> Response:
    try:
        task = task_manager.get_or_404(task_id)
    except KeyError:
        return Response("Task not found", status=404, mimetype="text/plain")

    try:
        source_file = safe_join(task.task_dir / "source", preview_file)
    except UnsafePathError as e:
        return Response(f"路径不安全: {e}", status=400, mimetype="text/plain")

    if not source_file.is_file():
        return Response("文件不存在", status=404, mimetype="text/plain")

    md = read_text_with_fallback(source_file)
    if content_modifier:
        try:
            md = content_modifier(md)
        except AIError as e:
            return Response(f"AI 错误 ({e.kind}): {e}", status=502, mimetype="text/plain")
        except RuntimeError as e:
            return Response(str(e), status=503, mimetype="text/plain")

    try:
        html = md_to_html(md, source_file.parent, style_options.get("code_theme", "kate"))
        pdf = html_to_pdf_bytes(html, style_options)
    except RenderError as e:
        return Response(f"渲染错误 ({e.kind}): {e}", status=500, mimetype="text/plain")

    # 直接用 bytes 读页数，不再落盘
    page_count = get_pdf_page_count(pdf)

    resp = Response(pdf, mimetype="application/pdf")
    resp.headers["X-Page-Count"] = str(page_count)
    return resp


@app.route("/preview/original", methods=["POST"])
def preview_original():
    data = request.get_json(silent=True) or {}
    if not data.get("task_id") or not data.get("preview_file"):
        return jsonify(error="缺少 task_id / preview_file"), 400
    return _do_preview(
        data["task_id"],
        data["preview_file"],
        data.get("style_options") or {},
    )


@app.route("/preview/translated", methods=["POST"])
def preview_translated():
    from .config import TRANSLATION_PROMPT
    data = request.get_json(silent=True) or {}
    if not data.get("task_id") or not data.get("preview_file"):
        return jsonify(error="缺少 task_id / preview_file"), 400

    def modifier(content: str) -> str:
        if not ai_config.is_configured():
            raise RuntimeError("AI 服务未配置（请在 .env 中设置 AI_API_KEY 等）")
        return ai_client.translate(
            content,
            data.get("target_language", "English"),
            TRANSLATION_PROMPT,
        )

    return _do_preview(
        data["task_id"],
        data["preview_file"],
        data.get("style_options") or {},
        content_modifier=modifier,
    )


# -----------------------------------------------------------------------------
# 启动转换
# -----------------------------------------------------------------------------
@app.route("/start_conversion", methods=["POST"])
def start_conversion():
    data = request.get_json(silent=True) or {}
    try:
        task = task_manager.get_or_404(data["task_id"])
    except KeyError:
        return jsonify(error="无效 Task ID"), 404

    # 允许从终态重试（FAILURE/STOPPED/SUCCESS），便于用户重新跑
    if task.state in (TaskState.RUNNING, TaskState.PAUSED, TaskState.PREPARING):
        return jsonify(error=f"任务正在进行中: {task.state.value}"), 409
    if task.state == TaskState.STOPPING:
        return jsonify(error="任务正在停止中，请稍候"), 409

    export_mode = data.get("export_mode", "translated")
    if not ai_config.is_configured() and export_mode != "original":
        return jsonify(
            error="AI 服务未配置，无法执行翻译。请在 .env 配置 AI_API_KEY / AI_API_URL / AI_MODEL"
        ), 400

    # 重试时清掉上次的产物 + 进度 + 日志
    task.reset_for_retry()
    task.append_log(
        f"任务已启动 (ID: {task.id}, 模式: {export_mode}, 语言: {data.get('target_language', 'English')})"
    )

    threading.Thread(
        target=run_conversion,
        args=(task, data.get("style_options") or {}, data.get("target_language", "English"), export_mode),
        daemon=True,
        name=f"conversion-{task.id[:8]}",
    ).start()
    return jsonify(task_id=task.id, message="Process started.")


# -----------------------------------------------------------------------------
# 任务控制
# -----------------------------------------------------------------------------
ALLOWED_ACTIONS = frozenset({"pause", "resume", "stop"})
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")


@app.route("/<action>/<task_id>", methods=["POST"])
def control_task(action, task_id):
    if action not in ALLOWED_ACTIONS or not _UUID_RE.match(task_id):
        return jsonify(error="无效操作"), 404
    try:
        task = task_manager.get_or_404(task_id)
    except KeyError:
        return jsonify(error="无效 Task ID"), 404
    if action == "pause":
        task.request_pause()
    elif action == "resume":
        task.request_resume()
    elif action == "stop":
        task.request_stop()
    return jsonify(message=f"Task {action} signal sent.")


# -----------------------------------------------------------------------------
# 状态 / 下载
# -----------------------------------------------------------------------------
@app.route("/status/<task_id>")
def task_status(task_id):
    try:
        task = task_manager.get_or_404(task_id)
    except KeyError:
        return jsonify(error="无效 Task ID"), 404
    return jsonify(
        state=task.state.value,
        progress=task.progress,
        current_file=task.current_file,
        logs=task.drain_logs(),
        error=task.error,
        result_url=task.result_url,
    )


@app.route("/download/<task_id>")
def download_result(task_id):
    try:
        task = task_manager.get_or_404(task_id)
    except KeyError:
        return jsonify(error="无效 Task ID"), 404
    if task.state != TaskState.SUCCESS:
        return jsonify(error=f"任务未完成，当前状态: {task.state.value}"), 400
    zip_name = f"Translated_Results_{task.id[:8]}.zip"
    zip_path = task.task_dir / zip_name
    if not zip_path.is_file():
        return jsonify(error="结果文件不存在或已被清理"), 404
    return send_from_directory(str(task.task_dir), zip_name, as_attachment=True)


# -----------------------------------------------------------------------------
# 启动横幅
# -----------------------------------------------------------------------------
def print_banner() -> None:
    """打印启动检查横幅。系统依赖（pandoc/GTK）只警告不阻止。"""
    print("=" * 20 + " 启动环境检查 " + "=" * 20)
    try:
        import pypandoc
        pypandoc.get_pandoc_version()
        print("[OK] Pandoc 已安装")
    except Exception:
        print("[WARN] Pandoc 未安装！仅'仅原文'模式可用。装好后重启即可。")
        print("       下载: https://github.com/jgreer/pandoc-windows/releases")
    try:
        import weasyprint  # noqa: F401
        print("[OK] weasyprint 可用")
    except Exception as e:
        print(f"[WARN] weasyprint 不可用 ({type(e).__name__})")
        print("       详见 https://doc.courtbouillon.org/weasyprint/stable/first_steps.html")
    try:
        __import__("pandas")
        print("[OK] pandas 可用")
    except ImportError:
        print("[FAIL] pandas 未安装，请 pip install pandas")
    if not ai_config.is_configured():
        print("[WARN] AI 服务未配置（AI_API_URL/AI_API_KEY/AI_MODEL）")
        print("       复制 .env.example 为 .env 并填入后重启。")
    else:
        print(f"[OK] AI 模型: {ai_config.model}")
    print("=" * 60)
    print("[ AI Document Translation Service ]")
    print(f"输出目录: {OUTPUT_DIR}")
    print("访问地址: http://127.0.0.1:5000")
    print("提示: pandoc/WeasyPrint 缺失不会阻止启动，相关功能会报错。")
    print("=" * 60)


if __name__ == "__main__":
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    print_banner()
    # threaded=True 让 Flask dev server 能并发处理预览/转换请求
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)
