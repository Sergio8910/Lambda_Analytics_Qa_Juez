"""GET /api/v1/scheduler/status — observabilidad del scheduler de monitores.
Antes el scheduler era una caja negra (corría en background sin forma de verlo).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from juez.api.main import app


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    import juez.api.monitor_store as monitor_store_mod

    with tempfile.TemporaryDirectory() as tmp:
        store = monitor_store_mod.MonitorStore(persist_dir=Path(tmp))
        monkeypatch.setattr(monitor_store_mod, "_store", store)
        monkeypatch.setattr(monitor_store_mod, "get_monitor_store", lambda: store)
        import juez.api.scheduler as scheduler_mod
        monkeypatch.setattr(scheduler_mod, "get_monitor_store", lambda: store)
        import juez.api.router_v1 as router_mod
        monkeypatch.setattr(router_mod, "get_monitor_store", lambda: store)
        yield TestClient(app)


def test_status_devuelve_estructura(client: TestClient) -> None:
    resp = client.get("/api/v1/scheduler/status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for clave in ("corriendo", "intervalo_poll_s", "monitores_totales",
                  "monitores_activos", "vencidos_ahora", "proximas_corridas",
                  "errores_recientes"):
        assert clave in data


def test_status_refleja_monitores_creados(client: TestClient) -> None:
    client.post("/api/v1/monitors", json={"nombre": "M1", "prompt": "Eres un agente.", "frecuencia": "daily"})
    data = client.get("/api/v1/scheduler/status").json()
    assert data["monitores_totales"] >= 1
    assert data["monitores_activos"] >= 1
