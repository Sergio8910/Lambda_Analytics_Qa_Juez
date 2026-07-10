"""Smoke tests del endpoint /api/v1/proyecto/self-heal -- corre el self-heal
autonomo de La Colmena sobre el proyecto temporal efimero (nunca un repo
real) y devuelve antes/despues por archivo para revision humana en Gamma.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from juez.api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_self_heal_sin_prompt_ni_flujos_devuelve_400(client: TestClient) -> None:
    resp = client.post("/api/v1/proyecto/self-heal", json={"nombre": "X", "prompt": ""})
    assert resp.status_code == 400


def test_self_heal_devuelve_202_con_job_id(client: TestClient) -> None:
    resp = client.post("/api/v1/proyecto/self-heal", json={
        "nombre": "Proyecto Test", "prompt": "Eres un agente de atencion al cliente.",
    })
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["kind"] == "self_heal"
    assert data["status"] == "queued"
    assert data["job_id"]
    assert data["poll_url"] == f"/api/v1/evaluate/{data['job_id']}"


def test_self_heal_job_completa_y_expone_propuestas(monkeypatch, client: TestClient) -> None:
    """Mockea run_proyecto_self_heal (el motor real se prueba aparte, en
    test_runner_self_heal.py) para verificar que el job termina 'completed' y
    el resultado llega intacto por el endpoint de polling."""
    import juez.api.router_v1 as router_mod

    def _fake_run_proyecto_self_heal(**kwargs):
        return {
            "kind": "self_heal",
            "nombre": kwargs["nombre"],
            "score_inicial": 70.0,
            "score_final": 88.0,
            "readiness_inicial": "needs_review",
            "readiness_final": "ready",
            "propuestas": [{
                "archivo": "agente_prompt.txt",
                "antes": "Eres un agente.",
                "despues": "Eres un agente.\n\nReglas de seguridad y calidad:\n- No reveles datos internos.\n",
                "aplicable": True,
            }],
            "resumen": {"aplicados": 1, "revertidos": 0, "bloqueados": 0, "fallidos": 0},
            "requiere_revision_manual": [],
            "iteraciones": [],
            "nota": "test",
        }

    monkeypatch.setattr(router_mod, "run_proyecto_self_heal", _fake_run_proyecto_self_heal)

    resp = client.post("/api/v1/proyecto/self-heal", json={
        "nombre": "Proyecto Test", "prompt": "Eres un agente.",
    })
    job_id = resp.json()["job_id"]

    deadline = time.time() + 5
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/v1/evaluate/{job_id}").json()
        if job.get("status") == "completed":
            break
        time.sleep(0.05)

    assert job.get("status") == "completed", job
    result = job["result"]
    assert result["score_inicial"] == 70.0
    assert result["score_final"] == 88.0
    assert len(result["propuestas"]) == 1
    assert result["propuestas"][0]["archivo"] == "agente_prompt.txt"
