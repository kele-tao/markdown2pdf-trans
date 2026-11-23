"""通用文件工具：编码回退读取、PDF 页数、文件名清洗。"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Union

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - 软依赖
    PdfReader = None  # type: ignore[assignment]


def read_text_with_fallback(path: Union[str, Path]) -> str:
    """先 utf-8-sig，再 utf-8，最后 gbk（忽略错误）。"""
    file_path = Path(path)
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            text = file_path.read_text(encoding=encoding)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="gbk", errors="ignore").replace("\r\n", "\n")


def get_pdf_page_count(source: Union[str, Path, bytes, io.IOBase]) -> int | str:
    """统计 PDF 页数。可接路径 / bytes / file-like。"""
    if PdfReader is None:
        return "N/A"
    try:
        if isinstance(source, (str, Path)):
            with open(source, "rb") as handle:
                return len(PdfReader(handle).pages)
        if isinstance(source, bytes):
            return len(PdfReader(io.BytesIO(source)).pages)
        return len(PdfReader(source).pages)
    except Exception:
        return "N/A"


_INVALID_FN_CHARS = re.compile(r'[\\/*?:"<>|]')


def sanitize_filename(name: str) -> str:
    """清洗文件名：去掉非法字符与多余空白，保留 CJK。"""
    value = _INVALID_FN_CHARS.sub("", str(name or ""))
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value)
    value = value.strip("._- ")
    return value[:120] or "untitled"
