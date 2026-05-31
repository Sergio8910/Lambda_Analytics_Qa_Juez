from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import importlib.util
import pytest

from juez.evaluation.api import server


def test_api_upload_rag(monkeypatch):
    if importlib.util.find_spec("multipart") is None:
        pytest.skip("python-multipart no esta instalado")
    monkeypatch.setenv("JUDGE_API_KEY", "secret-key")
    client = TestClient(server.app)

    payload = b'{"chunks": ["producto A: $10"]}'
    resp = client.post(
        "/v1/upload-rag",
        files={"file": ("demo_rag.json", payload, "application/json")},
        headers={"X-API-KEY": "secret-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    rag_path = Path(data["path"])
    assert rag_path.exists()

    rag_path.unlink(missing_ok=True)
    if rag_path.parent.exists() and not any(rag_path.parent.iterdir()):
        rag_path.parent.rmdir()
