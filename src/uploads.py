"""上传与解压：路径安全校验、ZIP 防护、大小限制。"""
from __future__ import annotations

import logging
import os
import shutil
import zipfile
from pathlib import Path

from werkzeug.utils import secure_filename

logger = logging.getLogger("m2p.uploads")

ALLOWED_MD = ".md"
ALLOWED_ZIP = ".zip"
MAX_UNCOMPRESSED_RATIO = 200  # zip bomb 防护：解压后/压缩前


class UnsafePathError(Exception):
    """检测到路径遍历或非法路径。"""


def safe_join(base: Path, *parts: str) -> Path:
    """把 parts 安全拼到 base 下，绝对禁止逃出 base。"""
    if not parts:
        return base.resolve()
    base_real = base.resolve()
    joined = base.joinpath(*parts) if parts else base
    target = joined.resolve()
    try:
        target.relative_to(base_real)
    except ValueError as exc:
        raise UnsafePathError(f"非法路径: {target} (base={base_real})") from exc
    return target


def sanitize_segment(name: str) -> str:
    """清洗单段文件名，保留中日韩字符（secure_filename 会吃掉非 ASCII）。"""
    cleaned = secure_filename(name)
    if cleaned:
        return cleaned
    # secure_filename 对纯中文返回空；退化为仅移除危险字符
    fallback = "".join(ch for ch in name if ch not in '\\/:*?"<>|\x00' and ch.strip())
    return fallback.strip(". ") or "unnamed"


def save_single_file(file_storage, dest_dir: Path, allowed_ext: str = ALLOWED_MD) -> str:
    """保存单个文件到 dest_dir，返回安全后的相对路径。"""
    if not file_storage or not file_storage.filename:
        raise ValueError("没有文件")
    filename = sanitize_segment(file_storage.filename)
    if not filename.lower().endswith(allowed_ext):
        raise ValueError(f"仅支持 {allowed_ext} 文件")
    dest = safe_join(dest_dir, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(dest)
    return filename


def save_folder_files(
    file_storages, dest_dir: Path, max_total_bytes: int = 100 * 1024 * 1024
) -> list[str]:
    """保存多文件（来自文件夹上传），保留相对路径结构。"""
    saved: list[str] = []
    total = 0
    for fs in file_storages:
        if not fs.filename:
            continue
        raw_parts = [part for part in Path(fs.filename.replace("\\", "/")).parts if part not in ("", "/", ".")]
        parts = [sanitize_segment(part) for part in raw_parts]
        if not parts:
            continue
        size = _stream_size(fs)
        total += size
        if total > max_total_bytes:
            raise ValueError(f"文件夹总大小超过限制 ({max_total_bytes // 1024 // 1024} MB)，已中止")
        dest = safe_join(dest_dir, *parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fs.save(dest)
        saved.append(dest.relative_to(dest_dir).as_posix())
    return saved


def _stream_size(file_storage) -> int:
    try:
        stream = file_storage.stream
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        return int(size)
    except Exception:  # pragma: no cover - 流不可 seek
        return 0


def fix_zip_name(name: str) -> str:
    """修复 Windows 中文 ZIP 乱码（cp437 → utf-8/gbk）。"""
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        try:
            return name.encode("cp437").decode("gbk")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return name


def unzip_safely(zip_path: Path, extract_dir: Path) -> tuple[list[str], list[str]]:
    """解压 zip，严格校验每条路径。返回 (saved, skipped)。"""
    extract_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    skipped: list[str] = []
    archived_total = 0
    extracted_total = 0

    with zipfile.ZipFile(zip_path, "r") as archive:
        archived_total = sum(member.compress_size for member in archive.infolist() if not member.is_dir())
        for member in archive.infolist():
            filename = fix_zip_name(member.filename)
            if filename.startswith("__MACOSX/") or filename.endswith("/__MACOSX"):
                continue
            if filename.startswith(("/", "\\")) or ".." in Path(filename).parts:
                logger.warning("跳过可疑 ZIP 条目：%s", filename)
                skipped.append(filename)
                continue
            raw_parts = [part for part in Path(filename.replace("\\", "/")).parts if part not in ("", "/", ".")]
            parts = [sanitize_segment(part) for part in raw_parts]
            if not parts:
                continue
            try:
                target = safe_join(extract_dir, *parts)
            except UnsafePathError:
                logger.warning("跳过路径穿越条目：%s", filename)
                skipped.append(filename)
                continue
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted_total += member.file_size
            if archived_total and extracted_total > archived_total * MAX_UNCOMPRESSED_RATIO:
                raise ValueError("ZIP 解压后体积异常（疑似压缩炸弹），已中止")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            saved.append(target.relative_to(extract_dir).as_posix())

    return saved, skipped


def collect_markdown_files(root: Path) -> list[str]:
    """收集 root 下所有 .md 文件（排除 ._ 开头），返回 posix 相对路径。"""
    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.lower().endswith(".md") and not name.startswith("._"):
                rel = (Path(dirpath) / name).relative_to(root).as_posix()
                out.append(rel)
    return sorted(out, key=str.casefold)


def tree_size(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total
