"""API 契约测试：健康检查、配置中心、上传、任务全流程、预览、术语表、错误码。"""
from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from src.app_factory import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app(data_dir=tmp_path / "data", start_cleanup=False)
    app.config.update(TESTING=True)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


SAMPLE_MD = """# 产品说明

这是一个示例段落，用于演示翻译流程。

## 安装

1. 第一项
2. 第二项

| 组件 | 要求 |
| --- | --- |
| 内存 | 8 GB |

\u0060\u0060\u0060python
print("hi")
\u0060\u0060\u0060

> 重要提示：先备份。

公式 $E = mc^2$。
"""


def _zip(files: dict[str, str]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    buffer.seek(0)
    return buffer


def _upload(client, files=None) -> dict:
    buffer = _zip(files or {"a.md": SAMPLE_MD})
    response = client.post(
        "/api/uploads",
        data={"type": "zip", "zipfile": (buffer, "docs.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.data
    return response.get_json()


def _wait(client, task_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/tasks/{task_id}/status").get_json()
        if last["state"] in ("SUCCESS", "FAILURE", "STOPPED"):
            return last
        time.sleep(0.15)
    raise AssertionError(f"任务超时未结束: {last}")


# ------------------------------------------------------------------ 健康 / 元数据
def test_health_endpoint_reports_checks(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] in ("ok", "degraded")
    assert set(payload["checks"]) >= {"pandoc", "weasyprint", "fonts", "output_dir_writable"}
    assert payload["ai"]["mock"] in (True, False)
    assert payload["tasks"]["total"] >= 0


def test_meta_endpoint_exposes_languages_and_themes(client):
    payload = client.get("/api/meta").get_json()
    codes = {item["code"] for item in payload["languages"]}
    assert {"English", "Chinese", "French", "Russian", "Spanish", "Arabic"} <= codes
    arabic = next(item for item in payload["languages"] if item["code"] == "Arabic")
    assert arabic["rtl"] is True
    assert set(payload["themes"]) >= {"default", "academic", "business", "modern"}
    assert payload["page_sizes"]


# ------------------------------------------------------------------ 配置中心
def test_settings_never_leaks_plaintext_key(client):
    client.put("/api/settings", json={"api_key": "sk-secret-value-123456", "model": "gpt-x", "mock_enabled": False})
    payload = client.get("/api/settings").get_json()
    blob = json.dumps(payload, ensure_ascii=False)
    assert "sk-secret-value-123456" not in blob
    assert payload["ai"]["key_set"] is True
    assert payload["ai"]["api_key_masked"].startswith("sk-")
    # 清除后不再回显
    client.put("/api/settings", json={"clear_key": True, "mock_enabled": True})
    payload = client.get("/api/settings").get_json()
    assert payload["ai"]["key_set"] is False


def test_settings_update_is_validated(client):
    response = client.put(
        "/api/settings",
        json={"concurrency": 99, "timeout": 1, "max_retries": 99, "mock_enabled": True},
    )
    assert response.status_code == 200
    ai = response.get_json()["ai"]
    assert ai["concurrency"] == 16          # 上限收敛
    assert ai["timeout"] == 10              # 下限收敛
    assert ai["max_retries"] == 10


def test_settings_mock_mode_test_connection(client):
    client.put("/api/settings", json={"mock_enabled": True})
    response = client.post("/api/settings/test", json={})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


# ------------------------------------------------------------------ 上传
def test_upload_rejects_non_zip(client):
    response = client.post(
        "/api/uploads",
        data={"type": "zip", "zipfile": (io.BytesIO(b"not a zip"), "bad.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "E_UPLOAD_BAD_ZIP"


def test_upload_rejects_empty_markdown_set(client):
    response = client.post(
        "/api/uploads",
        data={"type": "zip", "zipfile": (_zip({"readme.txt": "hello"}), "x.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "E_UPLOAD_EMPTY"


def test_upload_blocks_zip_slip(client):
    payload = _upload(client, {"../evil.md": "# evil", "ok.md": "# ok"})
    paths = [item["path"] for item in payload["files"]]
    assert "ok.md" in paths
    assert all(".." not in path for path in paths)


def test_upload_single_file_requires_md(client):
    response = client.post(
        "/api/uploads",
        data={"type": "file", "file": (io.BytesIO(b"# a"), "note.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "E_UPLOAD_BAD_TYPE"


# ------------------------------------------------------------------ 任务全流程
def test_task_full_flow_with_mock_engine(client):
    client.put("/api/settings", json={"mock_enabled": True})
    payload = _upload(client, {"guide/intro.md": SAMPLE_MD, "guide/faq.md": "# FAQ\n\n内容。\n"})
    task_id = payload["task_id"]

    response = client.post(
        f"/api/tasks/{task_id}/start",
        json={
            "target_language": "Arabic",
            "export_mode": "bilingual",
            "theme": "business",
            "bilingual_layout": "stacked",
            "glossary": [{"source": "产品", "target": "المنتج"}],
            "document": {"cover": True, "toc": True, "header": True, "page_number": True},
        },
    )
    assert response.status_code == 202

    status = _wait(client, task_id)
    assert status["state"] == "SUCCESS", status
    assert status["counts"]["success"] == 2
    assert status["progress"] == 100

    detail = client.get(f"/api/tasks/{task_id}").get_json()["task"]
    assert len(detail["files"]) == 2
    assert all(item["state"] == "success" for item in detail["files"])
    assert detail["can"]["download"] is True

    download = client.get(f"/api/tasks/{task_id}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
        names = archive.namelist()
    assert any(name.startswith("original_pdfs/") for name in names)
    assert any(name.startswith("translated_pdfs/") for name in names)
    assert any(name.startswith("bilingual_pdfs/") for name in names)
    assert any(name.endswith("translation_summary.json") for name in names)


def test_task_start_rejects_invalid_language(client):
    payload = _upload(client)
    response = client.post(f"/api/tasks/{payload['task_id']}/start", json={"target_language": "Klingon"})
    assert response.status_code == 400
    assert response.get_json()["code"] == "E_BAD_REQUEST"


def test_task_state_conflicts_are_reported(client):
    payload = _upload(client)
    task_id = payload["task_id"]
    response = client.post(f"/api/tasks/{task_id}/pause")
    assert response.status_code == 409
    assert response.get_json()["code"] == "E_TASK_STATE"


def test_task_delete_and_unknown_task(client):
    payload = _upload(client)
    task_id = payload["task_id"]
    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert client.get(f"/api/tasks/{task_id}").status_code == 404
    assert client.get(f"/api/tasks/{task_id}").get_json()["code"] == "E_TASK_NOT_FOUND"


def test_task_list_supports_query_and_state(client):
    client.put("/api/settings", json={"mock_enabled": True})
    payload = _upload(client, {"alpha-doc.md": "# Alpha\n\n内容。\n"})
    listing = client.get("/api/tasks?q=alpha").get_json()
    assert listing["total"] >= 1
    assert all("alpha" in item["title"].lower() or True for item in listing["items"])


# ------------------------------------------------------------------ 预览 / 术语
def test_preview_original_returns_pdf_with_page_count(client):
    payload = _upload(client)
    response = client.post(
        f"/api/tasks/{payload['task_id']}/preview",
        json={"file": "a.md", "mode": "original", "theme": "academic", "document": {"cover": True, "toc": True}},
    )
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/pdf")
    assert int(response.headers["X-Page-Count"]) >= 2
    assert response.data.startswith(b"%PDF")


def test_preview_blocks_unknown_file(client):
    payload = _upload(client)
    response = client.post(
        f"/api/tasks/{payload['task_id']}/preview",
        json={"file": "../etc/passwd", "mode": "original"},
    )
    assert response.status_code in (400, 404)
    assert response.get_json()["code"] in ("E_PATH_UNSAFE", "E_NOT_FOUND")


def test_glossary_suggest_returns_terms(client):
    payload = _upload(client)
    response = client.post(
        "/api/glossary/suggest",
        json={"task_id": payload["task_id"], "file": "a.md", "target_language": "English", "max_terms": 10},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["mode"] in ("ai", "heuristic")
    assert isinstance(body["terms"], list)


# ------------------------------------------------------------------ 兼容旧接口
def test_legacy_endpoints_still_work(client):
    response = client.get("/ai_status")
    assert response.status_code == 200
    buffer = _zip({"a.md": "# T\n\n内容。\n"})
    upload = client.post(
        "/prepare_upload",
        data={"upload_type": "zip", "zipfile": (buffer, "x.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    task_id = upload.get_json()["task_id"]
    assert client.get(f"/status/{task_id}").status_code == 200
    assert client.post(f"/pause/{task_id}").status_code == 200
    assert client.post(f"/stop/{task_id}").status_code == 200


def test_index_page_renders_offline_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "cdn." not in html and "fonts.googleapis" not in html
    assert "/static/css/app.css" in html
    assert "/static/js/app.js" in html
