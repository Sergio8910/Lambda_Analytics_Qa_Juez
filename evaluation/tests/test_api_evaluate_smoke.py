from __future__ import annotations

from fastapi.testclient import TestClient

from evaluation.api.app import app


def test_api_evaluate_smoke_replay():
    client = TestClient(app)
    payload = {
        "spec": {
            "run_id": "api-eval-test",
            "metrics": [
                {"name": "task_success_deterministic", "threshold": 0.7, "enabled": True},
                {"name": "format_compliance", "threshold": 1.0, "enabled": True},
            ],
        },
        "mode": "replay",
        "conversation": [
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "Hola, ¿en qué puedo ayudarte?"},
        ],
        "n_cases": 30,
        "audit_mode": "balanced",
    }
    resp = client.post("/v1/evaluate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    report = data.get("report", {})
    summary = report.get("summary", {})
    assert "executive_summary" in summary
