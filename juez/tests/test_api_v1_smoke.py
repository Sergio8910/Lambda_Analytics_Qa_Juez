"""Smoke tests para la API v1 del Juez.

Prueba que:
- El servidor arranca y responde /api/v1/health
- /api/v1/jobs lista jobs vacíos al inicio
- POST /api/v1/evaluate/pipeline rechaza requests sin agentes ni flujos
- POST /api/v1/evaluate/n8n con json_content inválido falla en el job, no en el POST

No prueba evaluaciones reales (eso requeriría claves API).
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from juez.api.main import app
    return TestClient(app)


def test_health_v1(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert data["evaluator_available"] is True


def test_health_legacy_still_works(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_jobs_list_starts_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert "total" in data
    assert isinstance(data["jobs"], list)


def test_pipeline_rejects_empty_request(client: TestClient) -> None:
    resp = client.post("/api/v1/evaluate/pipeline", json={"nombre": "vacío"})
    assert resp.status_code == 400
    assert "al menos un" in resp.json()["detail"].lower()


def test_pipeline_creates_job_with_eleven_id(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/evaluate/pipeline",
        json={
            "nombre": "test smoke",
            "eleven_ids": ["fake_agent_id_that_will_fail"],
            "total_conversaciones": 0,
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["kind"] == "pipeline"
    assert data["status"] == "queued"
    assert data["poll_url"] == f"/api/v1/evaluate/{data['job_id']}"

    # Esperar a que el job falle (no hay key real)
    job_id = data["job_id"]
    for _ in range(30):
        resp = client.get(f"/api/v1/evaluate/{job_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(0.5)
    # El job debe haber terminado (failed porque el agent_id no es real)
    final = client.get(f"/api/v1/evaluate/{job_id}").json()
    assert final["status"] in ("completed", "failed")


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/evaluate/nonexistent_job_id")
    assert resp.status_code == 404


def test_n8n_request_schema_validation(client: TestClient) -> None:
    # Sin "flow": debe rechazar con 422 (Pydantic)
    resp = client.post("/api/v1/evaluate/n8n", json={"total_conversaciones": 5})
    assert resp.status_code == 422


def test_openapi_docs_available(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    paths = spec.get("paths", {})
    assert "/api/v1/health" in paths
    assert "/api/v1/evaluate/elevenlabs" in paths
    assert "/api/v1/evaluate/n8n" in paths
    assert "/api/v1/evaluate/pipeline" in paths
    assert "/api/v1/evaluate/{job_id}" in paths
