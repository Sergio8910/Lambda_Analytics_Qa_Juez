"""Smoke tests de los endpoints /api/v1/monitors/* (crear, listar, pausar,
correr ahora, historial). Antes de esto, "monitoreo programado" era un script
CLI sin API ni historial consultable.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    import juez.api.monitor_store as monitor_store_mod

    with tempfile.TemporaryDirectory() as tmp:
        fresh_store = monitor_store_mod.MonitorStore(persist_dir=Path(tmp))
        monkeypatch.setattr(monitor_store_mod, "_store", fresh_store)
        monkeypatch.setattr(monitor_store_mod, "get_monitor_store", lambda: fresh_store)
        import juez.api.router_v1 as router_mod
        monkeypatch.setattr(router_mod, "get_monitor_store", lambda: fresh_store)
        # scheduler.ejecutar_monitor() (usado por /run-now) importa su propia
        # referencia de get_monitor_store -- parchear tambien ahi.
        import juez.api.scheduler as scheduler_mod
        monkeypatch.setattr(scheduler_mod, "get_monitor_store", lambda: fresh_store)

        from juez.api.main import app
        yield TestClient(app)


def _config(**overrides):
    base = {
        "nombre": "Monitor Emma", "prompt": "Eres un agente de atencion al cliente.",
        "frecuencia": "daily", "hora": "08:00",
    }
    base.update(overrides)
    return base


def test_crear_monitor_devuelve_id_y_next_run(client: TestClient) -> None:
    resp = client.post("/api/v1/monitors", json=_config())
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["id"]
    assert data["active"] is True
    assert data["next_run_at"] is not None
    assert data["config"]["nombre"] == "Monitor Emma"


def test_crear_monitor_sin_prompt_ni_agentes_devuelve_400(client: TestClient) -> None:
    resp = client.post("/api/v1/monitors", json=_config(prompt=""))
    assert resp.status_code == 400


def test_crear_monitor_con_reglas_negocio_y_reference_dataset(client: TestClient) -> None:
    resp = client.post("/api/v1/monitors", json=_config(
        reglas_negocio=["Nunca revelar informacion interna"],
        reference_dataset_id="abc123",
    ))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["config"]["reglas_negocio"] == ["Nunca revelar informacion interna"]
    assert data["config"]["reference_dataset_id"] == "abc123"


def test_get_monitor_inexistente_devuelve_404(client: TestClient) -> None:
    resp = client.get("/api/v1/monitors/no-existe")
    assert resp.status_code == 404


def test_listar_monitores_incluye_los_creados(client: TestClient) -> None:
    client.post("/api/v1/monitors", json=_config())
    resp = client.get("/api/v1/monitors")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


def test_pausar_y_reactivar_monitor(client: TestClient) -> None:
    creado = client.post("/api/v1/monitors", json=_config()).json()
    monitor_id = creado["id"]

    pausado = client.patch(f"/api/v1/monitors/{monitor_id}", json={"active": False})
    assert pausado.status_code == 200
    assert pausado.json()["active"] is False

    reactivado = client.patch(f"/api/v1/monitors/{monitor_id}", json={"active": True})
    assert reactivado.json()["active"] is True


def test_pausar_monitor_inexistente_devuelve_404(client: TestClient) -> None:
    resp = client.patch("/api/v1/monitors/no-existe", json={"active": False})
    assert resp.status_code == 404


def test_eliminar_monitor(client: TestClient) -> None:
    creado = client.post("/api/v1/monitors", json=_config()).json()
    monitor_id = creado["id"]

    resp = client.delete(f"/api/v1/monitors/{monitor_id}")
    assert resp.status_code == 204
    assert client.get(f"/api/v1/monitors/{monitor_id}").status_code == 404


def test_eliminar_monitor_inexistente_devuelve_404(client: TestClient) -> None:
    resp = client.delete("/api/v1/monitors/no-existe")
    assert resp.status_code == 404


def test_historial_de_monitor_recien_creado_esta_vacio(client: TestClient) -> None:
    creado = client.post("/api/v1/monitors", json=_config()).json()
    resp = client.get(f"/api/v1/monitors/{creado['id']}/historial")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["historial"] == []


def test_historial_de_monitor_inexistente_devuelve_404(client: TestClient) -> None:
    resp = client.get("/api/v1/monitors/no-existe/historial")
    assert resp.status_code == 404


def test_run_now_dispara_ejecucion_y_queda_en_historial(client: TestClient, monkeypatch) -> None:
    """run-now debe llamar run_proyecto (mockeado) y, tras esperar el thread,
    el historial debe reflejar la corrida."""
    import juez.api.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_proyecto", lambda **kw: {"score": 88.0, "estado": "LISTO"})

    creado = client.post("/api/v1/monitors", json=_config()).json()
    monitor_id = creado["id"]

    resp = client.post(f"/api/v1/monitors/{monitor_id}/run-now")
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"

    # run-now dispara un thread daemon -- darle un momento para completar.
    deadline = time.time() + 5
    historial = []
    while time.time() < deadline:
        historial = client.get(f"/api/v1/monitors/{monitor_id}/historial").json()["historial"]
        if historial:
            break
        time.sleep(0.1)

    assert historial, "El run-now no registro ninguna corrida en el historial a tiempo"
    assert historial[0]["score"] == 88.0


def test_run_now_monitor_inexistente_devuelve_404(client: TestClient) -> None:
    resp = client.post("/api/v1/monitors/no-existe/run-now")
    assert resp.status_code == 404
