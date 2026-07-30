"""
后台转换线程：执行 md → 翻译 → 渲染 → 打包。
状态变更通过 Task 对象，暂停/继续/停止通过 Event 协作。
"""
from __future__ import annotations

import logging
import os
import threading
import traceback
import zipfile
from pathlib import Path
from typing import Any, Optional

from .ai_client import AIError, ai_client
from .biligual_align import align_blocks, split_markdown_to_blocks
from .config import FILENAME_TRANSLATION_PROMPT, TRANSLATION_PROMPT
from .file_utils import get_pdf_page_count, read_text_with_fallback, sanitize_filename
from .renderer import RenderError, html_to_pdf_bytes, md_to_html
from .task_manager import Task, TaskState, WorkerStopped

logger = logging.getLogger(__name__)

# pandas 是软依赖
try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]


def _to_log(task: Task, msg: str) -> None:
    task.append_log(msg)
    logger.info("[%s] %s", task.id[:8], msg)


def _progress_for(i: int, total: int) -> int:
    """0..1 → 5..90% (前后留 5% 给报告+打包)"""
    if total <= 0:
        return 5
    return 5 + int((i / total) * 85)


def _collect_md_files(source_dir: Path) -> list[Path]:
    """收集 source_dir 下所有 .md 文件（rglob 已经按后缀过滤，._ 防御性再剔一遍）"""
    return sorted(p for p in source_dir.rglob("*.md") if not p.name.startswith("._"))


def _translate_with_fallback(
    task: Task, content: str, target_language: str, prompt: str, log_id: str
) -> Optional[str]:
    """翻译，失败时返回 None（不抛），并写日志。"""
    try:
        return ai_client.translate(
            content,
            target_language,
            prompt,
            log_callback=lambda m, t=task: _to_log(t, m),
        )
    except (AIError, Exception) as e:  # noqa: BLE001
        _to_log(task, f"  [WARN] 翻译失败 {log_id}: {e}，跳过")
        return None


def run_conversion(
    task: Task,
    style_options: dict[str, Any],
    target_language: str,
    export_mode: str,
) -> None:
    """主转换流程。"""
    if pd is None:
        task.set_state(TaskState.FAILURE)
        task.error = "pandas 未安装"
        _to_log(task, "[FAIL] pandas 未安装，无法生成报告")
        return

    task.set_state(TaskState.RUNNING)
    source_dir = task.task_dir / "source"
    result_dir = task.task_dir / "result"
    result_dir.mkdir(parents=True, exist_ok=True)

    try:
        files = _collect_md_files(source_dir)
    except Exception as e:
        task.set_state(TaskState.FAILURE)
        task.error = f"扫描源文件失败: {e}"
        _to_log(task, f"[FAIL] 扫描源文件失败: {e}")
        return

    if not files:
        task.set_state(TaskState.FAILURE)
        task.error = "未找到 .md 文件"
        _to_log(task, "[FAIL] 未找到 .md 文件")
        return

    total = len(files)
    _to_log(task, f"开始处理 {total} 个文件 (模式: {export_mode}, 语言: {target_language})")

    report_rows: list[dict[str, Any]] = []
    needs_translate = export_mode in ("translated", "bilingual")

    for i, file_path in enumerate(files):
        # 暂停/停止检查
        try:
            task.wait_if_paused()
        except WorkerStopped:
            task.set_state(TaskState.STOPPED)
            _to_log(task, f"[STOP] 任务已停止（已处理 {i}/{total}）")
            return

        rel_path = file_path.relative_to(source_dir).as_posix()
        task.current_file = rel_path
        task.progress = _progress_for(i, total)
        _to_log(task, f"({i+1}/{total}) 处理: {rel_path}")

        file_report = {
            "Original Filename": file_path.name,
            "Translated Filename": "N/A",
            "Original Pages": "N/A",
            "Translated Pages": "N/A",
            "Bilingual Pages": "N/A",
        }

        try:
            md_content = read_text_with_fallback(file_path)
            original_stem = file_path.stem
            translated_stem = original_stem
            translated_md: Optional[str] = None

            if needs_translate:
                translated_md = _translate_with_fallback(
                    task, md_content, target_language, TRANSLATION_PROMPT, rel_path
                )
                if translated_md is not None:
                    raw_stem = _translate_with_fallback(
                        task, original_stem, target_language, FILENAME_TRANSLATION_PROMPT, f"filename: {original_stem}"
                    )
                    if raw_stem is not None:
                        translated_stem = sanitize_filename(raw_stem)
                        file_report["Translated Filename"] = translated_stem + ".pdf"

            # 原文 PDF
            if export_mode in ("original", "bilingual"):
                try:
                    out = result_dir / "original_pdfs" / (Path(rel_path).with_suffix(".pdf").as_posix())
                    _render_one(task, md_content, file_path.parent, out, style_options)
                    file_report["Original Pages"] = get_pdf_page_count(out)
                except RenderError as e:
                    _to_log(task, f"  [WARN] 原文 PDF 生成失败 {rel_path}: {e}")

            # 译文 PDF（仅在翻译成功时生成）
            if needs_translate and translated_md is not None and export_mode in ("translated", "bilingual"):
                try:
                    rel_dir = Path(rel_path).parent
                    out = result_dir / "translated_pdfs" / rel_dir / f"{translated_stem}.pdf"
                    _render_one(task, translated_md, file_path.parent, out, style_options)
                    file_report["Translated Pages"] = get_pdf_page_count(out)
                except RenderError as e:
                    _to_log(task, f"  [WARN] 译文 PDF 生成失败 {rel_path}: {e}")

            # 双语对照 PDF
            if export_mode == "bilingual" and translated_md is not None:
                try:
                    rel_dir = Path(rel_path).parent
                    out = result_dir / "bilingual_pdfs" / rel_dir / f"{translated_stem}.pdf"
                    _render_bilingual(md_content, translated_md, file_path.parent, out, style_options)
                    file_report["Bilingual Pages"] = get_pdf_page_count(out)
                except RenderError as e:
                    _to_log(task, f"  [WARN] 双语 PDF 生成失败 {rel_path}: {e}")

        except WorkerStopped:
            task.set_state(TaskState.STOPPED)
            return
        except Exception as e:
            traceback.print_exc()
            _to_log(task, f"  [FAIL] 文件失败 {rel_path}: {e}")

        report_rows.append(file_report)

    # 汇总报告
    try:
        task.progress = 92
        _to_log(task, "生成汇总报告...")
        df = pd.DataFrame(report_rows)
        df.to_csv(result_dir / "translation_summary.csv", index=False, encoding="utf_8_sig")
    except Exception as e:
        _to_log(task, f"[WARN] 报告生成失败: {e}")

    # 打包
    try:
        task.progress = 97
        _to_log(task, "正在打包...")
        zip_path = task.task_dir / f"Translated_Results_{task.id[:8]}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, fns in os.walk(result_dir):
                for fn in fns:
                    fp = Path(root) / fn
                    zf.write(fp, fp.relative_to(result_dir))
        task.result_url = f"/download/{task.id}"
        task.progress = 100
        task.set_state(TaskState.SUCCESS)
        _to_log(task, "[DONE] 任务完成！可以下载了。")
    except Exception as e:
        traceback.print_exc()
        task.set_state(TaskState.FAILURE)
        task.error = f"打包失败: {e}"
        _to_log(task, f"[FAIL] 打包失败: {e}")


def _render_one(task: Task, md: str, md_file_dir: Path, out: Path, style_options: dict) -> None:
    """单文件渲染，捕获 RenderError 上抛（让调用方决定怎么处理）"""
    out.parent.mkdir(parents=True, exist_ok=True)
    html = md_to_html(md, md_file_dir, style_options.get("code_theme", "kate"))
    pdf = html_to_pdf_bytes(html, style_options)
    out.write_bytes(pdf)


def _render_bilingual(
    original_md: str,
    translated_md: str,
    md_file_dir: Path,
    output_path: Path,
    style_options: dict[str, Any],
) -> None:
    """结构化分块 + 渲染双语对照 PDF。"""
    ob = split_markdown_to_blocks(original_md)
    tb = split_markdown_to_blocks(translated_md)
    aligned = align_blocks(ob, tb)

    code_theme = style_options.get("code_theme", "kate")
    rows = []
    for o_block, t_block in aligned:
        o_html = md_to_html(o_block.content, md_file_dir, code_theme) if o_block.content.strip() else ""
        t_html = md_to_html(t_block.content, md_file_dir, code_theme) if t_block.content.strip() else ""
        rows.append(f"<tr><td>{o_html}</td><td>{t_html}</td></tr>")

    full_html = (
        "<html><body><table class='bilingual-table'>"
        + "".join(rows)
        + "</table></body></html>"
    )
    pdf = html_to_pdf_bytes(full_html, style_options)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf)
