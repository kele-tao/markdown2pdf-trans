"""预览接口：原文/译文的 Markdown 文本与 PDF 实时预览。"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from ..ai_client import AIError, ai_client
from ..config import is_rtl_language
from ..core.errors import AppError, ErrorCode
from ..file_utils import get_pdf_page_count, read_text_with_fallback
from ..pipeline.options import normalize_start_options
from ..renderer import RenderError, render_bilingual_to_pdf, render_markdown_to_pdf
from ..translation import GlossaryEntry, TranslationOptions, translate_markdown
from ..uploads import UnsafePathError, safe_join
from ._deps import get_task

logger = logging.getLogger("m2p.api.preview")
bp = Blueprint("preview", __name__, url_prefix="/api")


def _resolve_source(task, rel_path: str) -> Path:
    try:
        source = safe_join(task.task_dir / "source", rel_path)
    except UnsafePathError as exc:
        raise AppError(f"路径不安全：{exc}", code=ErrorCode.PATH_UNSAFE) from exc
    if not source.is_file():
        raise AppError(f"文件不存在：{rel_path}", code=ErrorCode.NOT_FOUND, status=404)
    return source


@bp.get("/tasks/<task_id>/files/<path:rel_path>")
def read_file(task_id: str, rel_path: str):
    task = get_task(task_id)
    source = _resolve_source(task, rel_path)
    markdown = read_text_with_fallback(source)
    mode = (request.args.get("mode") or "source").strip().lower()
    payload = {"path": rel_path, "markdown": markdown, "bytes": len(markdown.encode("utf-8")), "translated": False}
    if mode == "translated":
        entry = task.file_entry(rel_path)
        translated_name = entry.translated_name
        if translated_name:
            result_dir = task.task_dir / "result" / "translated_pdfs" / Path(rel_path).parent
            candidate = result_dir / translated_name
            if candidate.is_file():
                payload["markdown"] = _extract_pdf_text(candidate)
                payload["translated"] = True
                payload["pdf_path"] = str(candidate.relative_to(task.task_dir))
    return jsonify(payload)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # pragma: no cover
        return "（PDF 文本抽取失败，请直接预览 PDF）"


@bp.post("/tasks/<task_id>/preview")
def preview_pdf(task_id: str):
    task = get_task(task_id)
    data = request.get_json(silent=True) or {}
    rel_path = str(data.get("file") or data.get("preview_file") or "").strip()
    if not rel_path:
        raise AppError("缺少 file 参数", code=ErrorCode.BAD_REQUEST)
    mode = str(data.get("mode") or "original").strip().lower()
    source = _resolve_source(task, rel_path)
    markdown = read_text_with_fallback(source)
    options = _as_options(data, task)
    started = time.perf_counter()

    translated_md = None
    if mode == "translated":
        translated_md = _translate_for_preview(task, markdown, options)

    try:
        if mode == "bilingual" and translated_md is not None:
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as tmp:
                out = Path(tmp) / "preview.pdf"
                render_bilingual_to_pdf(
                    markdown,
                    translated_md,
                    source.parent,
                    out,
                    options["style_options"],
                    document=options["document"],
                    target_language=options["target_language"],
                    layout=options["bilingual_layout"],
                    fallback_title=source.stem,
                )
                pdf = out.read_bytes()
        else:
            from tempfile import TemporaryDirectory

            body = translated_md if (mode == "translated" and translated_md is not None) else markdown
            with TemporaryDirectory() as tmp:
                out = Path(tmp) / "preview.pdf"
                render_markdown_to_pdf(
                    body,
                    source.parent,
                    out,
                    options["style_options"],
                    document=options["document"],
                    target_language=options["target_language"],
                    fallback_title=source.stem,
                )
                pdf = out.read_bytes()
    except RenderError as exc:
        raise AppError(f"渲染失败（{exc.kind}）：{exc}", code=ErrorCode.RENDER_FAILED, status=500) from exc

    response = Response(pdf, mimetype="application/pdf")
    response.headers["X-Page-Count"] = str(get_pdf_page_count(pdf))
    response.headers["X-Elapsed-Ms"] = str(int((time.perf_counter() - started) * 1000))
    response.headers["X-Direction"] = "rtl" if is_rtl_language(options["target_language"]) else "ltr"
    return response


def _as_options(data: dict, task) -> dict:
    merged = dict(task.options or {})
    for key in ("target_language", "export_mode", "theme", "tone", "bilingual_layout", "glossary", "document"):
        if key in data and data[key] not in (None, ""):
            merged[key] = data[key]
    if isinstance(data.get("style_options"), dict) and data["style_options"]:
        base = dict(merged.get("style_options") or {})
        base.update(data["style_options"])
        merged["style_options"] = base
    try:
        return normalize_start_options(merged)
    except ValueError as exc:
        raise AppError(str(exc), code=ErrorCode.BAD_REQUEST) from exc


def _translate_for_preview(task, markdown: str, options: dict) -> str:
    result = translate_markdown(
        markdown,
        TranslationOptions(
            target_language=options["target_language"],
            glossary=[GlossaryEntry(**item) for item in options["glossary"]],
            tone=options["tone"],
            concurrency=1,
            max_retries=1,
            cache_namespace="preview",
        ),
        ai_call=ai_client.ai_call,
    )
    if not result.markdown.strip():
        raise AppError("翻译结果为空", code=ErrorCode.AI_UPSTREAM, status=502)
    return result.markdown


# ---------------------------------------------------------------- 旧接口兼容
@bp.post("/preview/original")
def legacy_preview_original():
    data = request.get_json(silent=True) or {}
    return _legacy_preview(data, "original")


@bp.post("/preview/translated")
def legacy_preview_translated():
    data = request.get_json(silent=True) or {}
    return _legacy_preview(data, "translated")


def _legacy_preview(data: dict, mode: str):
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        raise AppError("缺少 task_id", code=ErrorCode.BAD_REQUEST)
    payload = dict(data)
    payload["file"] = payload.get("preview_file", "")
    payload["mode"] = mode
    return preview_pdf(task_id)
