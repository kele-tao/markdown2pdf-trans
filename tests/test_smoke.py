"""
端到端冒烟测试：覆盖关键 API 端点 + 任务生命周期。
不依赖 pandoc/weasyprint/AI，使用 monkeypatch mock。
"""
import io
import os
import sys
import time
import types
import zipfile
from pathlib import Path
from unittest import mock

import pytest

# 在导入 src.app 之前，注入一个假的 weasyprint（避免真去 load GTK）
_fake_weasyprint = types.ModuleType("weasyprint")
_fake_weasyprint.HTML = lambda string="", **kw: type("F", (), {"write_pdf": lambda self, stylesheets=None: b"%PDF-1.4 mock"})()
_fake_weasyprint.CSS = lambda string="": f"CSS[{string[:20]}]"
sys.modules.setdefault("weasyprint", _fake_weasyprint)

# fake pypandoc
_fake_pypandoc = types.ModuleType("pypandoc")
_fake_pypandoc.convert_text = lambda *a, **kw: f"<p>{a[0] if a else kw.get('source','')}</p>"
_fake_pypandoc.get_pandoc_version = lambda: "99.0"
sys.modules.setdefault("pypandoc", _fake_pypandoc)


@pytest.fixture(autouse=True)
def mock_heavy_deps(monkeypatch, tmp_path):
    monkeypatch.setenv("M2P_OUTPUT_DIR", str(tmp_path / "out"))
    # 给一个假的 AI 配置（避免业务逻辑在 start_conversion 处被 400 拒绝）
    monkeypatch.setenv("AI_API_URL", "http://test-ai")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODEL", "gpt-test")

    # 阻断真实 AI 调用
    def fake_translate(self, content, target_language, prompt_template="", log_callback=None):
        return f"[{target_language}]\n{content}"

    monkeypatch.setattr("src.ai_client.AIClient.translate", fake_translate)

    # 阻断 task_manager 启动 cleanup 线程
    monkeypatch.setattr("src.task_manager.TaskManager.start_cleanup", lambda self: None)

    yield


def test_index_loads():
    from src.app import app
    client = app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"AI" in r.data
    assert b"theme" in r.data.lower() or "\u4e3b\u9898".encode() in r.data


def test_ai_status_configured():
    """autouse fixture 设了 AI 配置，应该报 configured=True"""
    from src.app import app
    client = app.test_client()
    r = client.get("/ai_status")
    assert r.status_code == 200
    assert r.json["configured"] is True
    assert r.json["model"] == "gpt-test"


def test_ai_status_unconfigured(monkeypatch):
    """显式清空环境变量，应该报 configured=False"""
    from src.app import app
    monkeypatch.setenv("AI_API_URL", "")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("AI_MODEL", "")
    client = app.test_client()
    r = client.get("/ai_status")
    assert r.status_code == 200
    assert r.json["configured"] is False


def test_upload_zip_and_preview():
    from src.app import app
    client = app.test_client()

    # 准备一个 zip（用 ASCII 名字避免 zipfile 编码差异）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.md", "# Hello\n\nThis is a test.")
        zf.writestr("dir/sub.md", "Sub content.")
    buf.seek(0)

    r = client.post("/prepare_upload", data={
        "upload_type": "zip",
        "zipfile": (buf, "test.zip"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    data = r.get_json()
    assert "task_id" in data
    assert len(data["preview_files"]) == 2
    task_id = data["task_id"]

    # 预览原文
    r = client.post("/preview/original", json={
        "task_id": task_id,
        "preview_file": "hello.md",
        "style_options": {},
    })
    assert r.status_code == 200
    assert r.data.startswith(b"%PDF")
    assert "X-Page-Count" in r.headers


def test_zip_slip_blocked():
    from src.app import app
    client = app.test_client()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
        zf.writestr("safe.md", "ok")
    buf.seek(0)

    r = client.post("/prepare_upload", data={
        "upload_type": "zip",
        "zipfile": (buf, "evil.zip"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    files = r.get_json()["preview_files"]
    assert "safe.md" in files
    assert not any(".." in f for f in files)


def test_task_lifecycle_translate_only(tmp_path):
    """端到端跑一个翻译任务：upload → start → poll → download。"""
    from src.app import app
    client = app.test_client()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", "# Title\n\nHello world.")
        zf.writestr("b.md", "# B\n\nFoo bar.")
    buf.seek(0)
    r = client.post("/prepare_upload", data={
        "upload_type": "zip",
        "zipfile": (buf, "x.zip"),
    }, content_type="multipart/form-data")
    task_id = r.get_json()["task_id"]

    r = client.post("/start_conversion", json={
        "task_id": task_id,
        "target_language": "English",
        "export_mode": "translated",
        "style_options": {},
    })
    assert r.status_code == 200

    # 轮询等完成
    for _ in range(50):
        time.sleep(0.1)
        r = client.get(f"/status/{task_id}")
        s = r.get_json()
        if s["state"] in ("SUCCESS", "FAILURE"):
            break
    assert s["state"] == "SUCCESS", s
    assert s["result_url"] == f"/download/{task_id}"
    assert any("[English]" in log["log"] for log in s["logs"]) or len(s["logs"]) > 0

    # 下载
    r = client.get(f"/download/{task_id}")
    assert r.status_code == 200
    assert r.data[:4] == b"PK\x03\x04"  # ZIP


def test_pause_resume_stop():
    from src.app import app
    client = app.test_client()

    # 准备一个 task
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.md", "x")
    buf.seek(0)
    r = client.post("/prepare_upload", data={"upload_type": "zip", "zipfile": (buf, "x.zip")}, content_type="multipart/form-data")
    tid = r.get_json()["task_id"]

    r = client.post(f"/pause/{tid}")
    assert r.status_code == 200
    r = client.post(f"/resume/{tid}")
    assert r.status_code == 200
    r = client.post(f"/stop/{tid}")
    assert r.status_code == 200

    # 非法 action
    r = client.post(f"/garbage/{tid}")
    assert r.status_code == 404
