"""任务持久化与恢复测试：重启不丢历史、中断任务标记、删除与清理。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.task_manager import FileEntry, TaskManager, TaskState


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    return tmp_path / "tasks.json"


def _make_manager(tmp_path: Path, registry: Path, ttl: int = 3600) -> TaskManager:
    manager = TaskManager(
        base_output_dir=tmp_path / "output",
        ttl_seconds=ttl,
        cleanup_interval=999,
        registry_path=registry,
        persist=True,
    )
    return manager


def test_task_survives_restart(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry)
    task = manager.create()
    (task.task_dir / "source").mkdir(parents=True, exist_ok=True)
    (task.task_dir / "source" / "a.md").write_text("# a", encoding="utf-8")
    task.preview_files = ["a.md"]
    task.title = "示例文档"
    task.options = {"target_language": "English", "export_mode": "translated", "theme": "default"}
    task.files = [FileEntry(path="a.md", state="success", translated_pages=3)]
    task.progress = 100
    task.result_url = f"/download/{task.id}"
    task.set_state(TaskState.SUCCESS)
    manager.flush()

    reloaded = _make_manager(tmp_path, registry)
    restored = reloaded.get(task.id)
    assert restored is not None
    assert restored.state is TaskState.SUCCESS
    assert restored.title == "示例文档"
    assert restored.options["target_language"] == "English"
    assert restored.files[0].translated_pages == 3
    assert restored.result_url == f"/download/{task.id}"


def test_interrupted_task_is_marked_stopped_after_restart(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry)
    task = manager.create()
    task.title = "进行中"
    task.options = {"target_language": "Chinese", "export_mode": "bilingual"}
    task.files = [FileEntry(path="a.md", state="running")]
    task.set_state(TaskState.RUNNING)
    manager.flush()

    reloaded = _make_manager(tmp_path, registry)
    restored = reloaded.get(task.id)
    assert restored is not None
    assert restored.state is TaskState.STOPPED
    assert restored.error and "重启" in restored.error
    assert any("重启" in entry["message"] for entry in restored.snapshot_logs())


def test_task_without_directory_is_skipped_on_load(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry)
    task = manager.create()
    task.set_state(TaskState.SUCCESS)
    manager.flush()
    import shutil

    shutil.rmtree(task.task_dir)
    reloaded = _make_manager(tmp_path, registry)
    assert reloaded.get(task.id) is None


def test_corrupted_registry_does_not_crash_startup(tmp_path: Path, registry: Path):
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("{ this is not json", encoding="utf-8")
    manager = _make_manager(tmp_path, registry)
    assert manager.list_ids() == []


def test_registry_file_is_valid_json_lines(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry)
    task = manager.create()
    task.set_state(TaskState.READY)
    manager.flush()
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert any(record["id"] == task.id for record in payload["tasks"])


def test_delete_removes_registry_entry_and_directory(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry)
    task = manager.create()
    task.set_state(TaskState.SUCCESS)
    manager.flush()
    assert task.task_dir.exists()
    assert manager.delete(task.id) is True
    assert not task.task_dir.exists()
    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert all(record["id"] != task.id for record in payload["tasks"])


def test_cleanup_expired_only_removes_terminal_tasks(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry, ttl=0)
    done = manager.create()
    running = manager.create()
    done.set_state(TaskState.SUCCESS)
    running.set_state(TaskState.RUNNING)
    manager.cleanup_expired()
    assert manager.get(done.id) is None
    assert manager.get(running.id) is not None


def test_list_tasks_sorted_and_filtered(tmp_path: Path, registry: Path):
    manager = _make_manager(tmp_path, registry)
    first = manager.create()
    first.title = "Alpha 指南"
    first.created_at = 1000.0
    first.set_state(TaskState.SUCCESS)
    second = manager.create()
    second.title = "Beta 手册"
    second.created_at = 2000.0
    second.set_state(TaskState.FAILURE)

    page, total = manager.list_tasks(limit=10)
    assert total == 2
    assert page[0].id == second.id  # 新的在前

    page, total = manager.list_tasks(limit=10, state="SUCCESS")
    assert total == 1 and page[0].id == first.id

    page, total = manager.list_tasks(limit=10, query="beta")
    assert total == 1 and page[0].id == second.id


def test_file_entry_roundtrip():
    entry = FileEntry(
        path="dir/a.md",
        state="failed",
        original_pages=2,
        translated_pages="N/A",
        blocks_total=10,
        blocks_translated=8,
        warnings=["w1"],
        error="boom",
        translated_name="a_en.pdf",
        elapsed_ms=123,
        retryable=True,
    )
    restored = FileEntry.from_dict(entry.to_dict())
    assert restored == entry
    assert restored.to_dict()["pages"]["original"] == 2
