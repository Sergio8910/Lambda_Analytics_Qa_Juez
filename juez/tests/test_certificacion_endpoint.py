"""Smoke test del endpoint /api/v1/proyecto/certificar (ciclo completo:
analizar -> evaluar -> construir -> iterar -> certificar)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from juez.api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_sin_prompt_ni_flujos_devuelve_400(client: TestClient) -> None:
    resp = client.post("/api/v1/proyecto/certificar", json={"nombre": "X", "prompt": ""})
    assert resp.status_code == 400


def test_devuelve_202_con_job_id(client: TestClient) -> None:
    resp = client.post("/api/v1/proyecto/certificar", json={
        "nombre": "Proyecto Test", "prompt": "Eres un agente de atencion.", "auto_fix": False,
    })
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["kind"] == "certificacion"
    assert data["job_id"]


def test_job_completa_y_expone_certificado(monkeypatch, client: TestClient) -> None:
    import juez.api.router_v1 as router_mod

    def _fake(**kwargs):
        return {
            "kind": "certificacion", "certificado": True, "veredicto": "CERTIFICADO",
            "score_inicial": 70.0, "score_final": 90.0, "convergio": True,
            "motivo_parada": "sin_criticos_ni_altos", "rondas": [], "cobertura": {"completa": True},
        }
    monkeypatch.setattr(router_mod, "run_certificacion", _fake)

    job_id = client.post("/api/v1/proyecto/certificar", json={
        "nombre": "T", "prompt": "Eres un agente.",
    }).json()["job_id"]

    deadline = time.time() + 5
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/v1/evaluate/{job_id}").json()
        if job.get("status") == "completed":
            break
        time.sleep(0.05)
    assert job.get("status") == "completed", job
    assert job["result"]["veredicto"] == "CERTIFICADO"
