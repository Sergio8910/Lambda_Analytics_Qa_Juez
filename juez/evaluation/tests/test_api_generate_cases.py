from __future__ import annotations

from fastapi.testclient import TestClient

from juez.evaluation.api.app import app


def test_api_generate_cases_count_and_tags():
    client = TestClient(app)
    payload = {
        "spec": {"run_id": "api-gen-test", "metrics": []},
        "n_cases": 30,
    }
    resp = client.post("/v1/generate-cases", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    cases = data.get("cases", [])
    assert 30 <= len(cases) <= 50
    for c in cases:
        assert "tags" in c
        assert "severity" in c
