from __future__ import annotations

from fastapi.testclient import TestClient

from evaluation.api.app import app
from evaluation.autogen.agent_client import AgentHttpClient, AgentHttpResult


def test_autogen_calls_agent_http(monkeypatch):
    def _fake_call(self, payload):
        return AgentHttpResult(output="ok", latency_ms=1.0)

    monkeypatch.setattr(AgentHttpClient, "call", _fake_call, raising=True)
    client = TestClient(app)
    payload = {
        "agent_name": "demo",
        "prompt_base": "Responde en español.",
        "n_cases": 5,
        "metrics": ["task_success_deterministic"],
        "audit_mode": "balanced",
        "seed": 7,
        "agent_http": {"url": "http://fake", "headers": {}, "timeout_ms": 1000},
    }
    resp = client.post("/v1/autogen/evaluate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "report" in data
    assert "summary" in data["report"]
