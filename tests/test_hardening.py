"""
针对刚才修复的 bug 加测试：
- 状态机：stop 永远能响应
- 缓存：多线程安全
- 并发：限流器真的限制并发
- 上传：大文件拒绝
- 错误处理：AI 失败不挂任务
"""
import sys
import threading
import time
import types
import zipfile
import io
from pathlib import Path

import pytest

# 在 import src.app 之前，注入假依赖
for name, mod in {
    "weasyprint": (
        lambda string="", **kw: type("F", (), {"write_pdf": lambda self, stylesheets=None: b"%PDF-1.4 mock"})()
    ),
    "pypandoc": (lambda *a, **kw: f"<p>{a[0] if a else kw.get('source','')}</p>"),
}.items():
    m = types.ModuleType(name)
    if name == "weasyprint":
        m.HTML = mod
        m.CSS = lambda string="": f"CSS[{string[:20]}]"
    else:
        m.convert_text = mod
    sys.modules.setdefault(name, m)


from src.concurrency import RateLimiter, retry_with_backoff
from src.task_manager import Task, TaskManager, TaskState, WorkerStopped
from src.translation_cache import TranslationCache
from src.uploads import save_folder_files, UnsafePathError


# === RateLimiter ===
class TestRateLimiter:
    def test_limits_concurrency(self):
        lim = RateLimiter(2)
        active = 0
        peak = 0
        lock = threading.Lock()

        def worker():
            nonlocal active, peak
            with lim:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                with lock:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert peak <= 2

    def test_context_manager_releases(self):
        lim = RateLimiter(1)
        with lim: pass
        with lim: pass  # 第二次能进，证明释放了


# === retry_with_backoff ===
class TestRetry:
    def test_eventually_succeeds(self):
        attempts = {"n": 0}
        @retry_with_backoff(max_retries=3, base_delay=0.01, retry_on=(ValueError,))
        def f():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ValueError("nope")
            return "ok"
        assert f() == "ok"
        assert attempts["n"] == 3

    def test_gives_up_after_max(self):
        attempts = {"n": 0}
        @retry_with_backoff(max_retries=2, base_delay=0.01, retry_on=(ValueError,))
        def f():
            attempts["n"] += 1
            raise ValueError("always fail")
        with pytest.raises(ValueError):
            f()
        # 1 初次 + 2 重试 = 3 次
        assert attempts["n"] == 3


# === Task state machine ===
class TestTaskStateMachine:
    def test_stop_always_observable(self):
        """WAIT_IF_PAUSED 即使在 stop 后调用也要立即抛"""
        t = Task(id="x", task_dir=Path("/tmp/x"))
        t.request_stop()
        with pytest.raises(WorkerStopped):
            t.wait_if_paused()

    def test_terminal_state_locked(self):
        t = Task(id="x", task_dir=Path("/tmp/x"))
        t.set_state(TaskState.SUCCESS)
        t.set_state(TaskState.RUNNING)  # 不应改变
        assert t.state == TaskState.SUCCESS

    def test_resume_from_paused(self):
        t = Task(id="x", task_dir=Path("/tmp/x"))
        t.set_state(TaskState.RUNNING)
        t.request_pause()
        assert t.state == TaskState.PAUSED
        t.request_resume()
        assert t.state == TaskState.RUNNING
        # resume 后 wait 立刻返回
        t.wait_if_paused()  # 不抛

    def test_paused_blocks_but_stop_breaks(self):
        t = Task(id="x", task_dir=Path("/tmp/x"))
        t.set_state(TaskState.RUNNING)
        t.request_pause()

        result = []
        def worker():
            try:
                t.wait_if_paused()
                result.append("done")
            except WorkerStopped:
                result.append("stopped")
        th = threading.Thread(target=worker)
        th.start()
        time.sleep(0.1)
        assert result == []  # 还在暂停
        t.request_stop()
        th.join(timeout=2)
        assert result == ["stopped"]


# === TaskManager cleanup ===
class TestTaskManagerCleanup:
    def test_cleanup_removes_terminal_only(self, tmp_path):
        mgr = TaskManager(base_output_dir=tmp_path, ttl_seconds=0, cleanup_interval=999)
        t1 = mgr.create()
        t2 = mgr.create()
        t1.set_state(TaskState.SUCCESS)
        # t2 仍 RUNNING
        # 设 ttl=0，cleanup_expired 立即删
        mgr.cleanup_expired()
        assert mgr.get(t1.id) is None
        assert mgr.get(t2.id) is not None
        mgr.delete(t2.id)


# === Cache thread safety ===
class TestCacheThreadSafe:
    def test_concurrent_put_get(self):
        c = TranslationCache(max_entries=1000, ttl_seconds=60)
        errors = []

        def writer():
            try:
                for i in range(100):
                    c.put(f"k{i}", f"v{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(100):
                    c.get(f"k{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)] + \
                  [threading.Thread(target=reader) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert errors == []


# === File uploads ===
class TestFolderUploadSizeLimit:
    def _make_fs(self, name, content):
        data = content.encode("utf-8") if isinstance(content, str) else content
        fs = types.SimpleNamespace()
        fs.filename = name
        fs.stream = io.BytesIO(data)
        fs.save = lambda dest: Path(dest).write_bytes(data)
        return fs

    def test_under_limit_ok(self, tmp_path):
        files = [self._make_fs("a.md", "x" * 100), self._make_fs("b.md", "y" * 100)]
        save_folder_files(files, tmp_path, max_total_bytes=1000)
        assert (tmp_path / "a.md").exists()
        assert (tmp_path / "b.md").exists()

    def test_over_limit_raises(self, tmp_path):
        files = [self._make_fs("a.md", "x" * 1000), self._make_fs("b.md", "y" * 1000)]
        with pytest.raises(ValueError, match="超过限制"):
            save_folder_files(files, tmp_path, max_total_bytes=1500)
