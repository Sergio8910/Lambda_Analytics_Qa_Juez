"""Smoke test del endpoint /api/v1/analyze/path-coverage (estático, sin tokens)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from juez.api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _wf_con_if():
    return {
        "name": "wf",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
            {"name": "If", "type": "n8n-nodes-base.if", "id": "i", "parameters": {
                "conditions": {"conditions": [
                    {"leftValue": "={{ $json.tipo }}", "rightValue": "premium",
                     "operator": {"type": "string", "operation": "equals"}}]}}},
            {"name": "A", "type": "n8n-nodes-base.set", "id": "a", "parameters": {}},
            {"name": "B", "type": "n8n-nodes-base.set", "id": "b", "parameters": {}},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
            "If": {"main": [
                [{"node": "A", "type": "main", "index": 0}],
                [{"node": "B", "type": "main", "index": 0}],
            ]},
        },
    }


def test_sin_flujo_devuelve_400(client: TestClient) -> None:
    resp = client.post("/api/v1/analyze/path-coverage", json={"flow": {}})
    assert resp.status_code == 400


def test_devuelve_caminos_e_inputs_sugeridos(client: TestClient) -> None:
    resp = client.post("/api/v1/analyze/path-coverage", json={"flow": {"json_content": _wf_con_if()}})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["analisis_caminos"]["total_caminos"] == 2
    assert "If" in data["analisis_caminos"]["nodos_de_ramificacion"]
    payloads = [i["payload_sugerido"] for i in data["inputs_por_camino"]["inputs_por_camino"]]
    assert {"tipo": "premium"} in payloads
    assert any(p.get("tipo") != "premium" for p in payloads)
