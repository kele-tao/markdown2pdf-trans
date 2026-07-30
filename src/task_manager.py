"""
任务生命周期管理：
- TaskState: 状态枚举
- Task: 单个任务的不可变快照 + 日志缓冲
- TaskManager: 增删查 + 过期清理（线程安全）
- 暂停/继续/停止用 threading.Event 协作，状态机保证 stop 永远可响应
"""
from __future__ import annotations

import enum
import logging
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TaskState(str, enum.Enum):
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"  # 临时态：用户请求了 stop，工作线程即将收尾
    STOPPED = "STOPPED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


_TERMINAL = frozenset({TaskState.STOPPED, TaskState.SUCCESS, TaskState.FAILURE})


@dataclass(eq=False)  # 不参与默认 __eq__，避免 Lock 不可哈希报错
class Task:
    id: str
    task_dir: Path
    state: TaskState = TaskState.PREPARING
    progress: int = 0
    error: Optional[str] = None
    result_url: Optional[str] = None
    preview_files: list[str] = field(default_factory=list)
    logs: deque = field(default_factory=lambda: deque(maxlen=2000))

    # 控制信号
    pause_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    # 当前正在处理的文件（前端展示用）
    current_file: str = ""
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        # pause_event set = "未暂停"（默认就是 set）
        # stop_event clear = "未停止"
        self.pause_event.set()

    # -- 日志 --
    def append_log(self, msg: str) -> None:
        self.logs.append({"log": msg})

    def drain_logs(self) -> list[dict]:
        out = []
        while self.logs:
            out.append(self.logs.popleft())
        return out

    # -- 状态机 --
    def set_state(self, new_state: TaskState) -> None:
        with self.state_lock:
            old = self.state
            # 终态不可变
            if old in _TERMINAL and new_state not in _TERMINAL:
                logger.warning("Task %s 已终态 (%s)，拒绝转为 %s", self.id[:8], old, new_state)
                return
            self.state = new_state
            # 同步控制信号
            if new_state == TaskState.PAUSED:
                self.pause_event.clear()
            elif new_state in (TaskState.RUNNING, TaskState.READY):
                self.pause_event.set()
            if new_state in _TERMINAL or new_state == TaskState.STOPPING:
                # 终态或正在停：确保 stop 立即可观察
                self.stop_event.set()
                # 终态也保证 pause 不会卡
                self.pause_event.set()
            elif new_state in (TaskState.RUNNING, TaskState.READY, TaskState.PAUSED):
                # 重新启动新一轮时，重置 stop 信号
                if new_state == TaskState.READY or (
                    new_state == TaskState.RUNNING and old in (TaskState.PREPARING,)
                ):
                    self.stop_event.clear()
            logger.debug("Task %s state: %s -> %s", self.id[:8], old, new_state)

    def reset_for_retry(self) -> None:
        """重置任务以便重新运行（仅用于终态 → READY 转换，绕过终态不可变保护）。"""
        with self.state_lock:
            self.state = TaskState.READY
            self.progress = 0
            self.current_file = ""
            self.error = None
            self.result_url = None
            self.pause_event.set()
            self.stop_event.clear()
            self.logs.clear()

    def request_pause(self) -> None:
        with self.state_lock:
            if self.state == TaskState.RUNNING:
                self.state = TaskState.PAUSED
                self.pause_event.clear()
                logger.debug("Task %s paused", self.id[:8])
                self.logs.append({"log": "任务已暂停。"})

    def request_resume(self) -> None:
        with self.state_lock:
            if self.state == TaskState.PAUSED:
                self.state = TaskState.RUNNING
                self.pause_event.set()
                logger.debug("Task %s resumed", self.id[:8])
                self.logs.append({"log": "任务已恢复。"})

    def request_stop(self) -> None:
        with self.state_lock:
            if self.state in (TaskState.PREPARING, TaskState.READY, TaskState.RUNNING, TaskState.PAUSED):
                # 不直接设 STOPPED，让工作线程在下一个检查点自己收尾
                self.stop_event.set()
                self.pause_event.set()  # 解开 pause 阻塞
                self.logs.append({"log": "任务已被用户手动结束。"})

    def wait_if_paused(self) -> None:
        """
        在工作循环中调用：
        - 暂停时阻塞等待
        - 一旦 stop_event 被 set，立即抛 WorkerStopped
        - 不会"已经停了还在等待"
        """
        # 永远先看 stop（request_stop 会同时 set stop 和 pause，所以这里必须先看）
        if self.stop_event.is_set():
            raise WorkerStopped()
        # 等到 pause_event 被 set（恢复 OR 停止都会 set）
        # 用 0.3s 轮询是为了响应 stop
        while not self.pause_event.wait(timeout=0.3):
            if self.stop_event.is_set():
                raise WorkerStopped()
        # pause_event 被 set 了——可能是 resume，也可能是 request_stop 触发的
        # 两者关键区别：stop_event 是否被 set
        if self.stop_event.is_set():
            raise WorkerStopped()


class WorkerStopped(Exception):
    """工作线程检测到停止信号时抛。"""


class TaskManager:
    def __init__(self, base_output_dir: Path, ttl_seconds: int, cleanup_interval: int) -> None:
        self._dir = base_output_dir
        self._ttl = ttl_seconds
        self._cleanup_interval = cleanup_interval
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None

    # -- CRUD --
    def create(self) -> Task:
        task_id = str(uuid.uuid4())
        task_dir = self._dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        t = Task(id=task_id, task_dir=task_dir)
        with self._lock:
            self._tasks[task_id] = t
        return t

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_or_404(self, task_id: str) -> Task:
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        return t

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._tasks.keys())

    def delete(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.pop(task_id, None)
        if t is None:
            return False
        try:
            if t.task_dir.exists():
                shutil.rmtree(t.task_dir, ignore_errors=True)
        except Exception as e:
            logger.warning("Failed to remove task dir %s: %s", t.task_dir, e)
        return True

    # -- 后台清理 --
    def start_cleanup(self) -> None:
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return
        self._stop.clear()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, name="task-cleanup", daemon=True
        )
        self._cleanup_thread.start()

    def stop_cleanup(self) -> None:
        self._stop.set()

    def _cleanup_loop(self) -> None:
        # 包一层 try，单次异常不退出循环
        while not self._stop.wait(timeout=self._cleanup_interval):
            try:
                self.cleanup_expired()
            except Exception:
                logger.exception("cleanup_expired failed")

    def cleanup_expired(self) -> int:
        now = time.time()
        to_delete: list[str] = []
        with self._lock:
            for tid, t in self._tasks.items():
                if t.state in _TERMINAL and now - t.created_at >= self._ttl:
                    to_delete.append(tid)
        for tid in to_delete:
            self.delete(tid)
        if to_delete:
            logger.info("Cleaned up %d expired tasks", len(to_delete))
        return len(to_delete)
