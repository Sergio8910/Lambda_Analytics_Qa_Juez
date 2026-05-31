from __future__ import annotations

from fastapi.testclient import TestClient

from juez.evaluation.api import server
from juez.evaluation.n8n import analyze_workflow_with_diagnosis
from juez.evaluation.n8n.static_analysis import analyze_workflow


def _sample_n8n_workflow() -> dict:
    return {
        "id": "wf-1",
        "name": "Demo n8n Review",
        "active": False,
        "nodes": [
            {
                "id": "1",
                "name": "Webhook In",
                "type": "n8n-nodes-base.webhook",
                "parameters": {"path": "demo-review"},
            },
            {
                "id": "2",
                "name": "Fetch User",
                "type": "n8n-nodes-base.httpRequest",
                "credentials": {"httpHeaderAuth": {"id": "cred-1", "name": "Prod Header Auth"}},
                "parameters": {
                    "method": "GET",
                    "url": "https://api.example.com/users",
                    "sendHeaders": True,
                    "headerParameters": {
                        "parameters": [
                            {"name": "Authorization", "value": "Bearer sk-1234567890abcdefghijklmno"}
                        ]
                    },
                },
            },
            {
                "id": "3",
                "name": "Fetch User Copy",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {
                    "method": "GET",
                    "url": "https://api.example.com/users",
                },
            },
            {
                "id": "4",
                "name": "JS Transform",
                "type": "n8n-nodes-base.code",
                "parameters": {
                    "jsCode": "const user = $input.first().json; return [{ json: { name: user.name } }];"
                },
            },
            {
                "id": "5",
                "name": "JS Transform Copy",
                "type": "n8n-nodes-base.code",
                "parameters": {
                    "jsCode": "const user = $input.first().json; return [{ json: { name: user.name } }];"
                },
            },
            {
                "id": "6",
                "name": "AI Draft",
                "type": "@n8n/n8n-nodes-langchain.agent",
                "parameters": {
                    "prompt": "Responde de forma profesional y breve a la consulta del cliente con el contexto disponible."
                },
            },
            {
                "id": "7",
                "name": "AI Draft Copy",
                "type": "@n8n/n8n-nodes-langchain.agent",
                "parameters": {
                    "prompt": "Responde de forma profesional y breve a la consulta del cliente con el contexto disponible."
                },
            },
            {
                "id": "8",
                "name": "Broken Ref",
                "type": "n8n-nodes-base.set",
                "parameters": {
                    "email": "={{ $('Node Missing').first().json.email }}",
                },
            },
            {
                "id": "9",
                "name": "Lonely Node",
                "type": "n8n-nodes-base.set",
                "disabled": True,
                "parameters": {"value": "orphan"},
            },
        ],
        "connections": {
            "Webhook In": {
                "main": [[{"node": "Fetch User", "type": "main", "index": 0}]],
            },
            "Fetch User": {
                "main": [[{"node": "JS Transform", "type": "main", "index": 0}]],
            },
            "JS Transform": {
                "main": [[{"node": "AI Draft", "type": "main", "index": 0}]],
            },
            "AI Draft": {
                "main": [[{"node": "Broken Ref", "type": "main", "index": 0}]],
            },
        },
    }


def test_n8n_static_analysis_detects_core_findings():
    analysis = analyze_workflow(_sample_n8n_workflow(), include_graph=True)

    finding_ids = {finding.finding_id for finding in analysis.findings}
    assert "structure-unreachable-nodes" in finding_ids
    assert "logic-broken-node-references" in finding_ids
    assert "security-hardcoded-secrets" in finding_ids
    assert any(fid.startswith("redundancy-http-") for fid in finding_ids)
    assert any(fid.startswith("redundancy-code-") for fid in finding_ids)
    assert analysis.scorecard.status in {"warning", "fail"}
    assert analysis.inventory.total_nodes == 9
    assert analysis.graph is not None
    assert "Lonely Node" in analysis.graph.disconnected_nodes


def test_n8n_analysis_endpoint_requires_api_key(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "test-key")
    client = TestClient(server.app)

    response = client.post(
        "/v1/n8n/analyze",
        json={"workflow": _sample_n8n_workflow(), "include_graph": True},
    )

    assert response.status_code == 401


def test_n8n_analysis_endpoint_with_api_key(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(server.app)

    response = client.post(
        "/v1/n8n/analyze",
        json={"workflow": _sample_n8n_workflow(), "include_graph": True},
        headers={"X-API-KEY": "test-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "analysis" in data
    assert "api_meta" in data
    assert data["analysis"]["inventory"]["workflow_name"] == "Demo n8n Review"
    assert "security-hardcoded-secrets" in {
        finding["finding_id"] for finding in data["analysis"]["findings"]
    }
    assert data["analysis"]["diagnosis"]["source"] == "fallback"
    assert data["analysis"]["diagnosis"]["priority_findings"]
    assert data["analysis"]["diagnosis"]["recommended_actions"]


def test_n8n_diagnosis_fallback_is_embedded(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    analysis, warnings = analyze_workflow_with_diagnosis(
        _sample_n8n_workflow(),
        include_graph=True,
        diagnosis_mode="auto",
    )

    assert not warnings
    assert analysis.diagnosis is not None
    assert analysis.diagnosis.source == "fallback"
    assert analysis.diagnosis.verdict
    assert analysis.diagnosis.priority_findings
    assert analysis.diagnosis.unknowns


def test_n8n_analysis_openapi_path(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "test-key")
    client = TestClient(server.app)

    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json().get("paths", {})
    assert "/v1/n8n/analyze" in paths
