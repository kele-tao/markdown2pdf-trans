"""任务生命周期与持久化。

- 状态机 + 暂停/继续/取消信号（Event 协作）
- 文件级进度（FileEntry）与结构化日志（带级别与时间戳）
- 注册表落盘到 data/tasks.json，重启后历史仍在；中断任务标为 STOPPED
"""
from __future__ import annotations

import enum
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class TaskState(str, enum.Enum):
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


TERMINAL_STATES = frozenset({TaskState.STOPPED, TaskState.SUCCESS, TaskState.FAILURE})
_ACTIVE_STATES = frozenset(
    {TaskState.PREPARING, TaskState.READY, TaskState.RUNNING, TaskState.PAUSED, TaskState.STOPPING}
)
_PERSIST_LOGS = 400


_LEVELS = {
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "success": logging.INFO,
}


class WorkerStopped(Exception):
    """工作线程检测到停止信号时抛。"""


class TaskDeleted(Exception):
    """任务在处理中被删除。"""


@dataclass
class FileEntry:
    """单个源文件在任务中的处理状态。"""

    path: str
    state: str = "pending"  # pending|running|success|failed|skipped
    original_pages: int | str | None = None
    translated_pages: int | str | None = None
    bilingual_pages: int | str | None = None
    blocks_total: int = 0
    blocks_translated: int = 0
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None
    translated_name: Optional[str] = None
    elapsed_ms: int = 0
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "state": self.state,
            "pages": {
                "original": self.original_pages,
                "translated": self.translated_pages,
                "bilingual": self.bilingual_pages,
            },
            "blocks_total": self.blocks_total,
            "blocks_translated": self.blocks_translated,
            "warnings": list(self.warnings),
            "error": self.error,
            "translated_name": self.translated_name,
            "elapsed_ms": self.elapsed_ms,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileEntry":
        pages = data.get("pages") or {}
        return cls(
            path=str(data.get("path", "")),
            state=str(data.get("state", "pending")),
            original_pages=pages.get("original"),
            translated_pages=pages.get("translated"),
            bilingual_pages=pages.get("bilingual"),
            blocks_total=int(data.get("blocks_total") or 0),
            blocks_translated=int(data.get("blocks_translated") or 0),
            warnings=list(data.get("warnings") or []),
            error=data.get("error"),
            translated_name=data.get("translated_name"),
            elapsed_ms=int(data.get("elapsed_ms") or 0),
            retryable=bool(data.get("retryable")),
        )


@dataclass(eq=False)  # Lock 不可哈希，禁用默认 __eq__
class Task:
    id: str
    task_dir: Path
    state: TaskState = TaskState.PREPARING
    progress: int = 0
    error: Optional[str] = None
    result_url: Optional[str] = None
    preview_files: list[str] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    logs: deque = field(default_factory=lambda: deque(maxlen=2000))
    pause_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    current_file: str = ""
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    title: str = ""
    persist_callback: Optional[Callable[["Task"], None]] = None

    def __post_init__(self) -> None:
        self.pause_event.set()  # set = 未暂停

    # ------------------------------------------------------------ 日志
    def append_log(self, message: str, level: str = "info") -> None:
        entry = {"ts": round(time.time(), 3), "level": level, "message": message}
        with self.state_lock:
            self.logs.append(entry)
            self.updated_at = time.time()
        logger.log(
            _LEVELS.get(level, logging.INFO),
            "[%s] %s",
            self.id[:8],
            message,
            extra={"task_id": self.id, "log_event": "task.log"},
        )

    def drain_logs(self) -> list[dict[str, Any]]:
        with self.state_lock:
            out = list(self.logs)
            self.logs.clear()
        return out

    def snapshot_logs(self) -> list[dict[str, Any]]:
        with self.state_lock:
            return list(self.logs)

    # ------------------------------------------------------------ 状态机
    def set_state(self, new_state: TaskState) -> None:
        with self.state_lock:
            old = self.state
            if old in TERMINAL_STATES and new_state not in TERMINAL_STATES:
                logger.warning("Task %s 已终态 (%s)，拒绝转为 %s", self.id[:8], old, new_state)
                return
            self.state = new_state
            self.updated_at = time.time()
            if new_state == TaskState.PAUSED:
                self.pause_event.clear()
            elif new_state in (TaskState.RUNNING, TaskState.READY):
                self.pause_event.set()
            if new_state in TERMINAL_STATES or new_state == TaskState.STOPPING:
                self.stop_event.set()
                self.pause_event.set()
                if not self.finished_at:
                    self.finished_at = time.time()
            elif new_state == TaskState.READY:
                self.stop_event.clear()
            if new_state == TaskState.RUNNING and not self.started_at:
                self.started_at = time.time()
        logger.debug("Task %s state: %s -> %s", self.id[:8], old, new_state)
        self.persist()

    def persist(self) -> None:
        callback = self.persist_callback
        if callback is not None:
            try:
                callback(self)
            except Exception:  # 持久化失败不应打断转换
                logger.exception("Task %s 持久化失败", self.id[:8])

    def reset_for_retry(self) -> None:
        """终态 → READY，清空上一轮产物与进度。"""
        with self.state_lock:
            self.state = TaskState.READY
            self.progress = 0
            self.current_file = ""
            self.error = None
            self.result_url = None
            self.pause_event.set()
            self.stop_event.clear()
            self.logs.clear()
            self.finished_at = 0.0
            self.updated_at = time.time()
        self.persist()

    def request_pause(self) -> bool:
        with self.state_lock:
            if self.state != TaskState.RUNNING:
                return False
            self.state = TaskState.PAUSED
            self.pause_event.clear()
            self.logs.append({"ts": round(time.time(), 3), "level": "warn", "message": "任务已暂停。"})
        self.persist()
        return True

    def request_resume(self) -> bool:
        with self.state_lock:
            if self.state != TaskState.PAUSED:
                return False
            self.state = TaskState.RUNNING
            self.pause_event.set()
            self.logs.append({"ts": round(time.time(), 3), "level": "info", "message": "任务已恢复。"})
        self.persist()
        return True

    def request_stop(self) -> bool:
        with self.state_lock:
            if self.state not in _ACTIVE_STATES:
                return False
            self.stop_event.set()
            self.pause_event.set()
            self.logs.append({"ts": round(time.time(), 3), "level": "warn", "message": "任务已被用户取消。"})
        self.persist()
        return True

    def wait_if_paused(self) -> None:
        if self.stop_event.is_set():
            raise WorkerStopped()
        while not self.pause_event.wait(timeout=0.3):
            if self.stop_event.is_set():
                raise WorkerStopped()
        if self.stop_event.is_set():
            raise WorkerStopped()

    # ------------------------------------------------------------ 文件进度
    def file_entry(self, path: str) -> FileEntry:
        for entry in self.files:
            if entry.path == path:
                return entry
        return FileEntry(path=path)

    def counts(self) -> dict[str, int]:
        success = sum(1 for f in self.files if f.state == "success")
        failed = sum(1 for f in self.files if f.state == "failed")
        done = sum(1 for f in self.files if f.state in ("success", "failed", "skipped"))
        return {"total": len(self.files), "done": done, "success": success, "failed": failed}

    # ------------------------------------------------------------ 序列化
    def summary(self) -> dict[str, Any]:
        counts = self.counts()
        elapsed = 0
        if self.started_at:
            end = self.finished_at or time.time()
            elapsed = int((end - self.started_at) * 1000)
        return {
            "id": self.id,
            "state": self.state.value,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title or (Path(self.preview_files[0]).stem if self.preview_files else self.id[:8]),
            "file_count": counts["total"],
            "success_count": counts["success"],
            "failed_count": counts["failed"],
            "target_language": self.options.get("target_language", ""),
            "export_mode": self.options.get("export_mode", ""),
            "theme": self.options.get("theme", ""),
            "result_url": self.result_url,
            "elapsed_ms": elapsed,
            "error": self.error,
        }

    def detail(self, include_logs: bool = True) -> dict[str, Any]:
        payload = self.summary()
        payload.update(
            {
                "files": [entry.to_dict() for entry in self.files],
                "current_file": self.current_file,
                "logs": self.snapshot_logs() if include_logs else [],
                "options": self.options,
                "can": {
                    "start": self.state in (TaskState.READY, TaskState.STOPPED, TaskState.FAILURE, TaskState.SUCCESS),
                    "pause": self.state == TaskState.RUNNING,
                    "resume": self.state == TaskState.PAUSED,
                    "cancel": self.state in (TaskState.RUNNING, TaskState.PAUSED, TaskState.PREPARING, TaskState.READY),
                    "retry": any(f.state == "failed" for f in self.files),
                    "download": self.state == TaskState.SUCCESS and bool(self.result_url),
                    "delete": self.state in TERMINAL_STATES,
                },
            }
        )
        return payload

    def to_record(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "id": self.id,
                "state": self.state.value,
                "progress": self.progress,
                "error": self.error,
                "result_url": self.result_url,
                "preview_files": list(self.preview_files),
                "files": [entry.to_dict() for entry in self.files],
                "options": dict(self.options),
                "logs": list(self.logs)[-_PERSIST_LOGS:],
                "current_file": self.current_file,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "title": self.title,
            }

    @classmethod
    def from_record(cls, record: dict[str, Any], task_dir: Path) -> "Task":
        task = cls(
            id=str(record["id"]),
            task_dir=task_dir,
            state=TaskState(record.get("state", "STOPPED")),
            progress=int(record.get("progress") or 0),
            error=record.get("error"),
            result_url=record.get("result_url"),
            preview_files=list(record.get("preview_files") or []),
            files=[FileEntry.from_dict(item) for item in record.get("files") or []],
            options=dict(record.get("options") or {}),
            created_at=float(record.get("created_at") or time.time()),
            updated_at=float(record.get("updated_at") or time.time()),
            started_at=float(record.get("started_at") or 0.0),
            finished_at=float(record.get("finished_at") or 0.0),
            title=str(record.get("title") or ""),
            current_file=str(record.get("current_file") or ""),
        )
        for entry in record.get("logs") or []:
            if isinstance(entry, dict):
                task.logs.append(
                    {
                        "ts": entry.get("ts", 0),
                        "level": entry.get("level", "info"),
                        "message": entry.get("message") or entry.get("log") or "",
                    }
                )
        return task


class TaskManager:
    """任务注册表：内存 + JSON 落盘。线程安全。"""

    def __init__(
        self,
        base_output_dir: Path,
        ttl_seconds: int = 86400,
        cleanup_interval: int = 1800,
        registry_path: Path | None = None,
        persist: bool = True,
    ) -> None:
        self._dir = Path(base_output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds
        self._cleanup_interval = cleanup_interval
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._persist = persist
        self._registry_path = registry_path or (self._dir.parent / "tasks.json")
        self._last_save: dict[str, float] = {}
        if persist:
            self._load()

    # ------------------------------------------------------------ 持久化
    def _load(self) -> None:
        path = self._registry_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("任务注册表读取失败（%s），忽略历史", exc)
            return
        records = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return
        recovered = 0
        for record in records:
            try:
                task_id = str(record["id"])
                task_dir = self._dir / task_id
                if not task_dir.exists():
                    continue
                task = Task.from_record(record, task_dir)
                if task.state in _ACTIVE_STATES:
                    task.state = TaskState.STOPPED
                    task.error = "服务重启导致任务中断，可点击重新开始"
                    task.finished_at = task.finished_at or time.time()
                    task.logs.append(
                        {
                            "ts": round(time.time(), 3),
                            "level": "warn",
                            "message": "服务重启，任务已标记为中断。",
                        }
                    )
                    recovered += 1
                task.persist_callback = self._save_task
                self._tasks[task.id] = task
            except Exception:
                logger.exception("恢复任务失败: %s", record.get("id"))
        logger.info("已从注册表恢复 %d 个任务（其中 %d 个中断）", len(self._tasks), recovered)

    def _save_all(self) -> None:
        if not self._persist:
            return
        with self._lock:
            records = [task.to_record() for task in self._tasks.values()]
        records.sort(key=lambda item: item.get("created_at", 0))
        payload = {"version": 1, "tasks": records}
        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._registry_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self._registry_path)
        except Exception:
            logger.exception("任务注册表写入失败")

    def _save_task(self, task: Task) -> None:
        if not self._persist:
            return
        now = time.time()
        # 同一任务 0.5s 内最多落盘一次，避免高频进度写放大
        if now - self._last_save.get(task.id, 0) < 0.5:
            return
        self._last_save[task.id] = now
        self._save_all()

    def flush(self) -> None:
        self._last_save.clear()
        self._save_all()

    # ------------------------------------------------------------ CRUD
    def create(self, task_id: str | None = None) -> Task:
        new_id = task_id or str(uuid.uuid4())
        task_dir = self._dir / new_id
        task_dir.mkdir(parents=True, exist_ok=True)
        task = Task(id=new_id, task_dir=task_dir)
        task.persist_callback = self._save_task
        with self._lock:
            self._tasks[new_id] = task
        task.persist()
        return task

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_or_404(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._tasks.keys())

    def list_tasks(self, limit: int = 20, offset: int = 0, state: str = "", query: str = "") -> tuple[list[Task], int]:
        with self._lock:
            items = list(self._tasks.values())
        if state:
            items = [task for task in items if task.state.value == state.upper()]
        if query:
            needle = query.strip().casefold()
            items = [
                task
                for task in items
                if needle in (task.title or "").casefold()
                or any(needle in path.casefold() for path in task.preview_files)
            ]
        items.sort(key=lambda task: task.created_at, reverse=True)
        total = len(items)
        page = items[offset : offset + limit]
        return page, total

    def delete(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            self._tasks.pop(task_id, None)
        shutil.rmtree(task.task_dir, ignore_errors=True)
        self._save_all()
        return True

    # ------------------------------------------------------------ 清理
    def start_cleanup(self) -> None:
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return
        self._stop.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, name="task-cleanup", daemon=True)
        self._cleanup_thread.start()

    def stop_cleanup(self) -> None:
        self._stop.set()

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(timeout=self._cleanup_interval):
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("cleanup_expired failed")

    def is_busy(self, task_id: str) -> bool:
        task = self.get(task_id)
        return bool(task) and task.state in (
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.PREPARING,
            TaskState.STOPPING,
        )

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [
                task.id
                for task in self._tasks.values()
                if task.state in TERMINAL_STATES and now - task.created_at >= self._ttl
            ]
        for task_id in expired:
            self.delete(task_id)
        if expired:
            logger.info("已清理 %d 个过期任务", len(expired))
        return len(expired)
