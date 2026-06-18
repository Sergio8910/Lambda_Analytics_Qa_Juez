"""Tests de la evaluación 24/7 reactiva (recibe fallo de n8n -> reporte)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from juez.api.failure_eval import extract_failure_info, run_on_failure


def _payload_con_workflow_inline():
    """Payload del errorTrigger que YA incluye el JSON del flujo (sin red)."""
    return {
        "execution": {
            "id": "exec-1",
            "url": "https://n8n.example.com/executions/exec-1",
            "error": {"message": "boom en el nodo", "node": {"name": "HTTP Request"}},
            "lastNodeExecuted": "HTTP Request",
        },
        "workflow": {
            "id": "wf-1",
            "name": "flujo_demo",
            "nodes": [
                {"id": "1", "name": "Webhook", "type": "n8n-nodes-base.webhook", "parameters": {"path": "x"}},
                {"id": "2", "name": "HTTP Request", "type": "n8n-nodes-base.httpRequest",
                 "parameters": {"method": "GET", "url": "https://api.example.com"}},
            ],
            "connections": {"Webhook": {"main": [[{"node": "HTTP Request", "type": "main", "index": 0}]]}},
        },
    }


def test_extract_failure_info():
    info = extract_failure_info(_payload_con_workflow_inline())
    assert info["workflow_id"] == "wf-1"
    assert info["workflow_name"] == "flujo_demo"
    assert info["failed_node"] == "HTTP Request"
    assert "boom" in info["error_message"]
    assert info["execution_url"].endswith("exec-1")


def test_extract_tolerante_a_error_string():
    info = extract_failure_info({"execution": {"error": "fallo plano"}, "workflow": {"name": "x"}})
    assert info["error_message"] == "fallo plano"


def test_run_on_failure_con_workflow_inline():
    r = run_on_failure(_payload_con_workflow_inline(), with_diagnosis=False)
    assert r["status"] == "done"
    assert r["analizado"] is True
    assert r["workflow_name"] == "flujo_demo"
    assert r["failed_node"] == "HTTP Request"
    assert r["score"] is not None
    txt = r["reporte_txt"]
    assert "FALLO DETECTADO" in txt
    assert "flujo_demo" in txt
    assert "HTTP Request" in txt
    assert "boom en el nodo" in txt
    assert "ANÁLISIS DEL FLUJO" in txt


def test_run_on_failure_sin_workflow_reporta_contexto():
    # Sin workflow.id y sin nodes inline -> no puede analizar, pero reporta el fallo.
    r = run_on_failure({"execution": {"error": {"message": "x"}}}, with_diagnosis=False)
    assert r["status"] == "done"
    assert r["analizado"] is False
    assert "FALLO DETECTADO" in r["reporte_txt"]


def test_endpoint_on_failure_devuelve_202():
    from juez.api.main import app

    client = TestClient(app)
    resp = client.post("/api/v1/evaluate/on-failure", json=_payload_con_workflow_inline())
    assert resp.status_code == 202
    body = resp.json()
    assert body["kind"] == "failure"
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["poll_url"].endswith(body["job_id"])
