"""Tests del scheduler de monitores: ejecutar_monitor() debe llamar
run_proyecto() (la Colmena moderna) con la config guardada, registrar el
resultado en el historial, y NUNCA lanzar -- un monitor que falla se
registra con status='failed' en vez de tumbar el ciclo del scheduler."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from juez.api.monitor_store import MonitorStore
import juez.api.scheduler as scheduler_mod


@pytest.fixture
def store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        s = MonitorStore(persist_dir=Path(tmp))
        monkeypatch.setattr(scheduler_mod, "get_monitor_store", lambda: s)
        yield s


def _config(**overrides):
    base = {
        "nombre": "Monitor Emma", "prompt": "Eres un agente.",
        "frecuencia": "daily", "hora": "08:00",
        "reglas_negocio": ["Nunca revelar info interna"],
        "reference_dataset_id": "abc123",
    }
    base.update(overrides)
    return base


def test_ejecutar_monitor_llama_run_proyecto_con_la_config(store, monkeypatch):
    capturado = {}

    def _fake_run_proyecto(**kwargs):
        capturado.update(kwargs)
        return {"score": 93.5, "estado": "NECESITA_AJUSTES", "problemas": []}

    import juez.api.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_proyecto", _fake_run_proyecto)

    monitor = store.create(_config())
    entrada = scheduler_mod.ejecutar_monitor(monitor)

    assert capturado["nombre"] == "Monitor Emma"
    assert capturado["reglas_negocio"] == ["Nunca revelar info interna"]
    assert capturado["reference_dataset_id"] == "abc123"
    assert entrada["status"] == "completed"
    assert entrada["score"] == 93.5
    assert entrada["estado"] == "NECESITA_AJUSTES"


def test_ejecutar_monitor_registra_historial_en_el_store(store, monkeypatch):
    import juez.api.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_proyecto", lambda **kw: {"score": 80.0, "estado": "LISTO"})

    monitor = store.create(_config())
    scheduler_mod.ejecutar_monitor(monitor)

    actualizado = store.get(monitor["id"])
    assert len(actualizado["historial"]) == 1
    assert actualizado["historial"][0]["score"] == 80.0


def test_ejecutar_monitor_no_lanza_si_run_proyecto_falla(store, monkeypatch):
    import juez.api.runner as runner_mod

    def _falla(**kw):
        raise ConnectionError("webhook no responde")

    monkeypatch.setattr(runner_mod, "run_proyecto", _falla)

    monitor = store.create(_config())
    entrada = scheduler_mod.ejecutar_monitor(monitor)  # no debe lanzar

    assert entrada["status"] == "failed"
    assert "ConnectionError" in entrada["error"]
    assert "webhook no responde" in entrada["error"]


def test_componentes_desde_config_aplica_defaults_razonables():
    kwargs = scheduler_mod._componentes_desde_config({"nombre": "X", "prompt": "Y"})
    assert kwargs["total_conversaciones"] == 10
    assert kwargs["concurrencia"] == 3
    assert kwargs["modo_ejecucion"] == "sandbox"
    assert kwargs["eleven_ids"] == []
    assert kwargs["n8n_flows"] == []
    assert kwargs["reglas_negocio"] == []
    assert kwargs["objetivos"] is None
    assert kwargs["reference_dataset_id"] is None


def test_scheduler_start_es_idempotente_no_duplica_threads():
    sched = scheduler_mod.MonitorScheduler(intervalo_s=60)
    sched.start()
    primer_thread = sched._thread
    sched.start()
    assert sched._thread is primer_thread
    sched.stop()
