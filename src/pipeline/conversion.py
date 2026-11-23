"""后台转换编排：翻译 → 排版 → 打包。

设计要点：
- 翻译走 `src.translation` 引擎（结构切分/上下文/术语/校验重试），不再直连 AI 客户端
- 每文件独立 try/except：单文件失败不影响整批，失败原因写入 FileEntry
- 支持暂停/继续/取消；取消时未完成块保留原文并标记可重试
- 只重试指定失败文件（retry_files）
"""
from __future__ import annotations

import logging
import os
import threading
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from ..ai_client import AIError, ai_client
from ..config import MAX_CHARS_PER_CHUNK
from ..core.settings_store import get_settings
from ..file_utils import get_pdf_page_count, read_text_with_fallback, sanitize_filename
from ..renderer import RenderError, render_bilingual_to_pdf, render_markdown_to_pdf
from ..task_manager import FileEntry, Task, TaskState, WorkerStopped
from ..translation import (
    GlossaryEntry,
    TranslationOptions,
    translate_filename,
    translate_markdown,
)
from .options import normalize_start_options
from .report import summarize, write_report

logger = logging.getLogger("m2p.pipeline")

_PDF_KINDS = ("original", "translated", "bilingual")


def _log(task: Task, message: str, level: str = "info") -> None:
    task.append_log(message, level)


def _check_stop(task: Task) -> None:
    """在耗时工序之间插检查点：暂停/取消都能尽快生效，而不是等整篇渲染完。"""
    if task.stop_event.is_set():
        raise WorkerStopped()
    task.wait_if_paused()


def _collect_md_files(source_dir: Path) -> list[Path]:
    return sorted(
        (path for path in source_dir.rglob("*.md") if not path.name.startswith("._")),
        key=lambda path: path.as_posix().casefold(),
    )


def _progress(done: int, total: int, start: int = 8, span: int = 84) -> int:
    if total <= 0:
        return start
    return start + int((done / total) * span)


class _Cancelled(Exception):
    """内部取消信号。"""


def run_conversion(
    task: Task,
    payload: dict[str, Any] | None = None,
    *,
    retry_files: Iterable[str] | None = None,
) -> None:
    """任务主流程。异常绝不外抛（线程入口）。"""
    try:
        _run(task, payload or {}, retry_files=retry_files)
    except WorkerStopped:
        task.set_state(TaskState.STOPPED)
        _log(task, "[STOP] 任务已取消，已完成部分保留。", "warn")
    except _Cancelled:
        task.set_state(TaskState.STOPPED)
        _log(task, "[STOP] 任务已取消。", "warn")
    except Exception as exc:  # noqa: BLE001 - 最后的兜底
        logger.exception("转换流程异常")
        task.error = f"转换失败：{exc}"
        task.set_state(TaskState.FAILURE)
        _log(task, f"[FAIL] 转换流程异常：{exc}", "error")


def _run(task: Task, payload: dict[str, Any], *, retry_files: Iterable[str] | None = None) -> None:
    settings = get_settings()
    try:
        options = normalize_start_options(payload)
    except ValueError as exc:
        task.error = str(exc)
        task.set_state(TaskState.FAILURE)
        _log(task, f"[FAIL] 配置非法：{exc}", "error")
        return

    export_mode = options["export_mode"]
    language = options["target_language"]
    needs_translate = export_mode in ("translated", "bilingual")
    if needs_translate and not settings.is_configured() and not settings.is_mock():
        task.error = "AI 服务未配置，无法翻译。请在配置中心填写地址/密钥/模型，或启用离线演示模式。"
        task.set_state(TaskState.FAILURE)
        _log(task, f"[FAIL] {task.error}", "error")
        return

    task.options = {**options, "mode": "mock" if settings.is_mock() else "live"}
    task.set_state(TaskState.RUNNING)
    if not task.title:
        task.title = _derive_title(task)

    source_dir = task.task_dir / "source"
    result_dir = task.task_dir / "result"
    result_dir.mkdir(parents=True, exist_ok=True)

    files = _collect_md_files(source_dir)
    if not files:
        task.error = "源目录中没有 .md 文件"
        task.set_state(TaskState.FAILURE)
        _log(task, "[FAIL] 源目录中没有 .md 文件", "error")
        return

    retry_set = {str(item) for item in retry_files} if retry_files else None
    if retry_set is not None:
        files = [path for path in files if path.relative_to(source_dir).as_posix() in retry_set]
        if not files:
            _log(task, "[WARN] 没有需要重试的文件", "warn")
            task.set_state(TaskState.SUCCESS)
            return
        _sync_file_entries(task, files, source_dir, preserve=retry_set)
    else:
        _sync_file_entries(task, files, source_dir)

    total = len(files)
    _log(
        task,
        f"开始处理 {total} 个文件（模式：{export_mode}，语言：{language}，"
        f"引擎：{'离线演示' if settings.is_mock() else '真实 AI'}）",
    )

    glossary_entries = [GlossaryEntry(**item) for item in options["glossary"]]
    translated_map: dict[str, str] = {}
    started_all = time.perf_counter()

    for index, file_path in enumerate(files):
        task.wait_if_paused()
        rel_path = file_path.relative_to(source_dir).as_posix()
        entry = task.file_entry(rel_path)
        entry.state = "running"
        task.current_file = rel_path
        task.progress = _progress(index, total)
        _log(task, f"({index + 1}/{total}) 处理 {rel_path}")
        started = time.perf_counter()

        try:
            original_md = _read_source(file_path)
            translated_md: Optional[str] = None
            translated_stem = file_path.stem

            _check_stop(task)
            if needs_translate:
                translated_md, blocks_total, blocks_translated, warnings = _translate_file(
                    task, original_md, file_path, language, options, glossary_entries
                )
                entry.blocks_total = blocks_total
                entry.blocks_translated = blocks_translated
                entry.warnings.extend(warnings)
                if blocks_translated < blocks_total:
                    entry.warnings.append(
                        f"有 {blocks_total - blocks_translated} 个内容块翻译失败并保留原文，详见任务日志。"
                    )
                translated_map[rel_path] = translated_md
                translated_stem = _translate_stem(task, original_md, file_path, language)
                entry.translated_name = f"{translated_stem}.pdf"

            _check_stop(task)
            if export_mode in ("original", "bilingual"):
                out = result_dir / "original_pdfs" / Path(rel_path).with_suffix(".pdf")
                _render(task, original_md, file_path, out, options, language, layout="none")
                entry.original_pages = get_pdf_page_count(out)

            _check_stop(task)
            if needs_translate and translated_md is not None:
                out = result_dir / "translated_pdfs" / Path(rel_path).parent / f"{translated_stem}.pdf"
                _render(task, translated_md, file_path, out, options, language, layout="none")
                entry.translated_pages = get_pdf_page_count(out)

            _check_stop(task)
            if export_mode == "bilingual" and translated_md is not None:
                out = result_dir / "bilingual_pdfs" / Path(rel_path).parent / f"{translated_stem}.pdf"
                out.parent.mkdir(parents=True, exist_ok=True)
                render_bilingual_to_pdf(
                    original_md,
                    translated_md,
                    file_path.parent,
                    out,
                    options["style_options"],
                    document=options["document"],
                    target_language=language,
                    layout=options["bilingual_layout"],
                    fallback_title=file_path.stem,
                )
                entry.bilingual_pages = get_pdf_page_count(out)

            entry.state = "success"
            entry.error = None
            entry.retryable = False
            _log(task, f"  完成 {rel_path}（{entry.translated_pages or entry.original_pages or 'N/A'} 页）", "success")
        except WorkerStopped:
            entry.state = "pending"
            raise
        except AIError as exc:
            entry.state = "failed"
            entry.error = f"AI 调用失败：{exc}"
            entry.retryable = True
            _log(task, f"  [FAIL] {rel_path}: {entry.error}", "error")
        except RenderError as exc:
            entry.state = "failed"
            entry.error = f"PDF 渲染失败（{exc.kind}）：{exc}"
            entry.retryable = True
            _log(task, f"  [FAIL] {rel_path}: {entry.error}", "error")
        except Exception as exc:  # noqa: BLE001 - 单文件失败不拖垮整批
            entry.state = "failed"
            entry.error = f"{type(exc).__name__}: {exc}"
            entry.retryable = True
            logger.exception("文件处理失败 %s", rel_path)
            _log(task, f"  [FAIL] {rel_path}: {entry.error}", "error")
            traceback.print_exc()
        finally:
            entry.elapsed_ms = int((time.perf_counter() - started) * 1000)
            task.progress = _progress(index + 1, total)
            task.current_file = ""
            task.persist()

    # 汇总 + 打包
    task.progress = 94
    _log(task, "生成汇总报告…")
    stats = summarize(task.files)
    write_report(
        result_dir,
        task.files,
        meta={
            "target_language": language,
            "export_mode": export_mode,
            "theme": options["theme"],
            "bilingual_layout": options["bilingual_layout"],
            "elapsed_ms": int((time.perf_counter() - started_all) * 1000),
            "stats": stats,
        },
    )

    if retry_set is not None:
        # 单文件重试：把新产物合并进已有 zip
        _merge_zip(task, result_dir)
    task.progress = 98
    _log(task, "打包结果…")
    zip_path = _zip_results(task, result_dir)

    task.result_url = f"/download/{task.id}"
    task.progress = 100
    if stats["failed"]:
        task.set_state(TaskState.FAILURE)
        task.error = (
            f"{stats['failed']} 个文件处理失败（共 {stats['total']}）："
            + "；".join(
                f"{entry.path}: {(entry.error or '')[:120]}" for entry in task.files if entry.state == "failed"
            )[:400]
        )
        _log(task, f"[FAIL] 任务完成但存在失败文件：{stats['failed']}/{stats['total']}，可在结果页单文件重试。", "error")
    else:
        task.set_state(TaskState.SUCCESS)
        _log(task, f"[DONE] 全部 {stats['total']} 个文件处理成功，可下载（{zip_path.name}）。", "success")


# --------------------------------------------------------------------------- 辅助


def _sync_file_entries(
    task: Task,
    files: list[Path],
    source_dir: Path,
    preserve: set[str] | None = None,
) -> None:
    existing = {entry.path: entry for entry in task.files}
    entries: list[FileEntry] = []
    for path in files:
        rel = path.relative_to(source_dir).as_posix()
        entry = existing.get(rel)
        if entry is None or preserve is None or rel not in preserve:
            entry = FileEntry(path=rel)
        entries.append(entry)
    full_map = {entry.path: entry for entry in entries}
    # 保留不在本次批次内的历史条目（例如全量重跑时的其他文件）
    for path, entry in existing.items():
        full_map.setdefault(path, entry)
    task.files = sorted(full_map.values(), key=lambda item: item.path.casefold())


def _read_source(file_path: Path) -> str:
    return read_text_with_fallback(file_path)


def _derive_title(task: Task) -> str:
    for path in task.preview_files:
        return Path(path).stem
    return task.id[:8]


def _translate_file(
    task: Task,
    original_md: str,
    file_path: Path,
    language: str,
    options: dict[str, Any],
    glossary_entries: list[GlossaryEntry],
) -> tuple[str, int, int, list[str]]:
    def on_progress(message: str) -> None:
        _log(task, f"  {message}")

    result = translate_markdown(
        original_md,
        TranslationOptions(
            target_language=language,
            glossary=glossary_entries,
            tone=options.get("tone", "professional"),
            max_chars_per_chunk=MAX_CHARS_PER_CHUNK,
            concurrency=get_settings().concurrency,
            cancel_event=task.stop_event,
            on_progress=on_progress,
            max_retries=2,
            cache_namespace=get_settings().model,
        ),
        ai_call=ai_client.ai_call,
    )
    warnings = list(result.warnings)
    if result.cancelled:
        raise _Cancelled()
    return result.markdown, result.blocks_total, result.blocks_translated, warnings


def _translate_stem(task: Task, original_md: str, file_path: Path, language: str) -> str:
    stem = file_path.stem
    try:
        translated = translate_filename(stem, language, ai_call=ai_client.ai_call)
    except Exception:  # 文件名翻译失败不影响主流程
        logger.warning("文件名翻译失败：%s", stem, exc_info=True)
        return sanitize_filename(stem)
    cleaned = sanitize_filename(translated or stem)
    return cleaned or sanitize_filename(stem)


def _render(
    task: Task,
    md: str,
    file_path: Path,
    out: Path,
    options: dict[str, Any],
    language: str,
    *,
    layout: str = "none",
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    render_markdown_to_pdf(
        md,
        file_path.parent,
        out,
        options["style_options"],
        document=options["document"],
        target_language=language,
        fallback_title=file_path.stem,
    )


def _zip_results(task: Task, result_dir: Path) -> Path:
    zip_path = task.task_dir / f"Translated_Results_{task.id[:8]}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, names in os.walk(result_dir):
            for name in names:
                full = Path(root) / name
                archive.write(full, full.relative_to(result_dir))
    return zip_path


def _merge_zip(task: Task, result_dir: Path) -> None:
    """单文件重试后合并旧 zip 中的其他产物，避免丢失。"""
    zip_path = task.task_dir / f"Translated_Results_{task.id[:8]}.zip"
    if not zip_path.is_file():
        return
    try:
        with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as archive:
            for root, _dirs, names in os.walk(result_dir):
                for name in names:
                    full = Path(root) / name
                    archive.write(full, full.relative_to(result_dir))
    except Exception:  # 合并失败则整体重打包
        logger.warning("合并 zip 失败，将重新打包", exc_info=True)


def start_conversion_thread(
    task: Task,
    payload: dict[str, Any],
    *,
    retry_files: Iterable[str] | None = None,
) -> threading.Thread:
    thread = threading.Thread(
        target=run_conversion,
        args=(task, payload),
        kwargs={"retry_files": retry_files},
        daemon=True,
        name=f"conversion-{task.id[:8]}",
    )
    thread.start()
    return thread
