from __future__ import annotations

from fastapi.testclient import TestClient

from evaluation.api.app import app


def test_openapi_contains_expected_paths():
    client = TestClient(app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    paths = data.get("paths", {})
    assert "/v1/generate-cases" in paths
    assert "/v1/evaluate" in paths
    assert "/v1/auto-evaluate" in paths
    assert "/health" in paths
