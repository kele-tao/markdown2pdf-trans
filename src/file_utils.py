"""
通用文件工具：编码回退读取、PDF 页数统计、文件名清洗。
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Union

try:
    from pypdf import PdfReader
except ImportError:  # 软依赖
    PdfReader = None  # type: ignore[assignment]


def read_text_with_fallback(path: Union[str, Path]) -> str:
    """先 utf-8-sig，再 utf-8，最后 gbk(忽略错误)。"""
    p = Path(path)
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="gbk", errors="ignore")


def get_pdf_page_count(source: Union[str, Path, bytes, io.IOBase]) -> int | str:
    """
    统计 PDF 页数。可接：
    - 文件路径（str / Path）
    - bytes
    - 任意 file-like 对象
    """
    if PdfReader is None:
        return "N/A"
    try:
        if isinstance(source, (str, Path)):
            with open(source, "rb") as f:
                return len(PdfReader(f).pages)
        if isinstance(source, bytes):
            return len(PdfReader(io.BytesIO(source)).pages)
        # file-like
        return len(PdfReader(source).pages)
    except Exception:
        return "N/A"


_INVALID_FN_CHARS = re.compile(r'[\\/*?:"<>|]')


def sanitize_filename(name: str) -> str:
    name = _INVALID_FN_CHARS.sub("", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("._") or "untitled"

