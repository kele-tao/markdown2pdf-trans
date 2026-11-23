"""端到端样例文档测试：样例库覆盖表格/代码/图片/脚注/公式，跑通三种导出模式。"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest

from src.app_factory import create_app

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "samples"


@pytest.fixture()
def app(tmp_path):
    app = create_app(data_dir=tmp_path / "data", start_cleanup=False)
    app.config.update(TESTING=True)
    app.put_settings = None  # 占位，避免误用
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _upload_samples(client) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(SAMPLES.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(SAMPLES).as_posix())
    buffer.seek(0)
    response = client.post(
        "/api/uploads",
        data={"type": "zip", "zipfile": (buffer, "samples.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.data
    payload = response.get_json()
    paths = {item["path"] for item in payload["files"]}
    assert "full_sample.md" in paths
    return payload["task_id"]


def _wait(client, task_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"/api/tasks/{task_id}/status").get_json()
        if last["state"] in ("SUCCESS", "FAILURE", "STOPPED"):
            return last
        time.sleep(0.2)
    raise AssertionError(f"任务超时: {last}")


@pytest.mark.parametrize(
    "mode,language,layout",
    [
        ("translated", "English", "side_by_side"),
        ("original", "Chinese", "stacked"),
        ("bilingual", "Arabic", "side_by_side"),
    ],
)
def test_sample_set_end_to_end(client, mode, language, layout):
    client.put("/api/settings", json={"mock_enabled": True})
    task_id = _upload_samples(client)
    response = client.post(
        f"/api/tasks/{task_id}/start",
        json={
            "target_language": language,
            "export_mode": mode,
            "bilingual_layout": layout,
            "theme": "business",
            "glossary": [{"source": "翻译引擎", "target": "translation engine"}],
            "document": {"cover": True, "toc": True, "header": True, "footer": True, "page_number": True},
        },
    )
    assert response.status_code == 202, response.data
    status = _wait(client, task_id)
    assert status["state"] == "SUCCESS", status
    assert status["counts"]["failed"] == 0
    detail = client.get(f"/api/tasks/{task_id}").get_json()["task"]
    for item in detail["files"]:
        assert item["state"] == "success", item
        pages = item["pages"]
        if mode == "original":
            assert pages["original"] and pages["original"] != "N/A"
        elif mode == "translated":
            assert pages["translated"] and pages["translated"] != "N/A"
        else:
            assert pages["bilingual"] and pages["bilingual"] != "N/A"

    download = client.get(f"/api/tasks/{task_id}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.data)) as archive:
        names = archive.namelist()
        summary = archive.read("translation_summary.json").decode("utf-8")
    assert any(name.endswith(".pdf") for name in names)
    assert "full_sample.md" in summary


def test_sample_document_structure_survives_translation(client):
    """结构保真：mock 引擎翻译后，Markdown 结构块序列必须与原文一致。"""
    from src.translation.markdown_parser import parse_markdown
    from src.ai_client import ai_client
    from src.translation import TranslationOptions, translate_markdown

    source = (SAMPLES / "full_sample.md").read_text(encoding="utf-8")
    result = translate_markdown(
        source,
        TranslationOptions("Chinese", concurrency=2, max_chars_per_chunk=6000, cache_namespace="sample"),
        ai_call=ai_client.ai_call,
    )
    assert result.failed_blocks == [], result.warnings
    assert result.blocks_translated > 0
    original = [(block.kind, block.level) for block in parse_markdown(source).blocks]
    translated = [(block.kind, block.level) for block in parse_markdown(result.markdown).blocks]
    assert translated == original
    # 代码块与公式原样保留
    assert "console.log" not in result.markdown and "pip install" not in result.markdown
    assert "$$" in result.markdown
