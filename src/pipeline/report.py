"""结果汇总：CSV + JSON 报告。"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("m2p.report")

_HEADERS = [
    "源文件",
    "译文文件名",
    "原文页数",
    "译文页数",
    "双语页数",
    "翻译块",
    "成功块",
    "状态",
    "耗时(ms)",
    "告警",
    "错误",
]


def write_report(result_dir: Path, files: list[Any], *, meta: dict[str, Any] | None = None) -> dict[str, Path]:
    """写 translation_summary.csv / .json，返回路径。"""
    result_dir.mkdir(parents=True, exist_ok=True)
    csv_path = result_dir / "translation_summary.csv"
    json_path = result_dir / "translation_summary.json"
    rows: list[list[Any]] = []
    payload_files: list[dict[str, Any]] = []
    for entry in files:
        data = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        pages = data.get("pages") or {}
        rows.append(
            [
                data.get("path", ""),
                data.get("translated_name") or "",
                pages.get("original") if pages.get("original") is not None else "N/A",
                pages.get("translated") if pages.get("translated") is not None else "N/A",
                pages.get("bilingual") if pages.get("bilingual") is not None else "N/A",
                data.get("blocks_total", 0),
                data.get("blocks_translated", 0),
                data.get("state", ""),
                data.get("elapsed_ms", 0),
                " / ".join(data.get("warnings") or [])[:500],
                data.get("error") or "",
            ]
        )
        payload_files.append(data)

    try:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_HEADERS)
            writer.writerows(rows)
    except OSError as exc:  # 汇总失败不应影响交付
        logger.warning("CSV 报告写入失败：%s", exc)

    try:
        json_path.write_text(
            json.dumps({"meta": meta or {}, "files": payload_files}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("JSON 报告写入失败：%s", exc)

    return {"csv": csv_path, "json": json_path}


def summarize(files: list[Any]) -> dict[str, Any]:
    success = sum(1 for entry in files if getattr(entry, "state", "") == "success")
    failed = sum(1 for entry in files if getattr(entry, "state", "") == "failed")
    warnings = sum(len(getattr(entry, "warnings", []) or []) for entry in files)
    return {"total": len(files), "success": success, "failed": failed, "warnings": warnings}
