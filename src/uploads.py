"""
文件上传、解压、路径安全校验。
"""
from __future__ import annotations

import logging
import os
import shutil
import zipfile
from pathlib import Path

from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)


class UnsafePathError(Exception):
    """检测到路径遍历或非法路径。"""


def safe_join(base: Path, *parts: str) -> Path:
    """把 parts 安全拼到 base 下，绝对禁止逃出 base。"""
    if not parts:
        return base.resolve()
    base_real = base.resolve()
    # 兼容 Python < 3.12 的 Path / *args 写法
    joined = base.joinpath(*parts) if parts else base
    target = joined.resolve()
    try:
        target.relative_to(base_real)
    except ValueError as e:
        raise UnsafePathError(f"非法路径: {target} (base={base_real})") from e
    return target


def save_single_file(file_storage, dest_dir: Path, allowed_ext: str = ".md") -> str:
    """保存单个文件到 dest_dir，返回安全后的相对路径。"""
    if not file_storage or not file_storage.filename:
        raise ValueError("没有文件")
    filename = secure_filename(file_storage.filename)
    if not filename.lower().endswith(allowed_ext):
        raise ValueError(f"仅支持 {allowed_ext} 文件")
    dest = safe_join(dest_dir, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    file_storage.save(dest)
    return filename


def save_folder_files(file_storages, dest_dir: Path, max_total_bytes: int = 100 * 1024 * 1024) -> list[str]:
    """
    保存多文件（来自文件夹上传），保留相对路径结构。
    累加字节超过 max_total_bytes 直接抛 ValueError。
    """
    saved: list[str] = []
    total = 0
    for fs in file_storages:
        if not fs.filename:
            continue
        parts = [secure_filename(p) for p in Path(fs.filename).parts if p not in ("", "/", "\\")]
        if not parts:
            continue
        # 累加 size（fs.content_length 可能为 None，对 stream 友好）
        size = 0
        try:
            fs.stream.seek(0, 2)  # seek 到末尾
            size = fs.stream.tell()
            fs.stream.seek(0)
        except Exception:
            size = 0
        total += size
        if total > max_total_bytes:
            raise ValueError(
                f"文件夹总大小超过限制 ({max_total_bytes // 1024 // 1024} MB)，已中止"
            )
        dest = safe_join(dest_dir, *parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fs.save(dest)
        saved.append(str(dest.relative_to(dest_dir)))
    return saved


def unzip_safely(zip_path: Path, extract_dir: Path) -> tuple[list[str], list[str]]:
    """
    解压 zip，严格校验每个条目路径，修复中文编码乱码问题。
    返回 (saved, skipped):
      - saved: 成功解压的文件相对路径
      - skipped: 被跳过的文件相对路径（不安全 / 损坏）
    """
    extract_dir.mkdir(parents=True, exist_ok=True)
    base_real = extract_dir.resolve()
    saved: list[str] = []
    skipped: list[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            # 1) 编码修复
            try:
                filename = member.filename.encode("cp437").decode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                try:
                    filename = member.filename.encode("cp437").decode("gbk")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    filename = member.filename

            # 2) 跳过 macOS 元数据
            if filename.startswith("__MACOSX/") or filename.endswith("/__MACOSX"):
                continue
            # 3) 跳过绝对路径或父目录引用
            if filename.startswith(("/", "\\")) or ".." in Path(filename).parts:
                logger.warning("Skipping suspicious zip entry: %s", filename)
                skipped.append(filename)
                continue

            # 4) secure_filename 二次清洗每一段
            parts = [secure_filename(p) for p in Path(filename).parts if p not in ("", "/", "\\")]
            if not parts:
                continue

            try:
                target = safe_join(extract_dir, *parts)
            except UnsafePathError:
                logger.warning("Skipping zip entry (path traversal): %s", filename)
                skipped.append(filename)
                continue

            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            saved.append(str(target.relative_to(extract_dir)))

    return saved, skipped


def collect_markdown_files(root: Path) -> list[str]:
    """收集 root 下所有 .md 文件（排除 ._ 开头），返回正斜杠路径。"""
    out: list[str] = []
    for dp, _dn, fns in os.walk(root):
        for fn in sorted(fns):
            if fn.lower().endswith(".md") and not fn.startswith("._"):
                # 统一用 / 跨平台
                rel = (Path(dp) / fn).relative_to(root).as_posix()
                out.append(rel)
    return out
