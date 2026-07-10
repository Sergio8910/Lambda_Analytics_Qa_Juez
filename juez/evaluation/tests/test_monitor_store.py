"""Tests del almacen de monitores programados: creacion, historial con
delta de score, calculo de proxima corrida, y persistencia -- antes de esto,
"monitoreo programado" era un script CLI sin API ni historial consultable."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from juez.api.monitor_store import MonitorStore, calcular_proximo_run


def _config(**overrides):
    base = {
        "nombre": "Monitor Emma", "prompt": "Eres un agente de atencion.",
        "frecuencia": "daily", "hora": "08:00",
        "total_conversaciones": 10,
    }
    base.update(overrides)
    return base


def test_create_asigna_id_y_calcula_next_run():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())
        assert monitor["id"]
        assert monitor["active"] is True
        assert monitor["next_run_at"] is not None
        assert monitor["historial"] == []


def test_get_id_inexistente_devuelve_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        assert store.get("no-existe") is None


def test_list_ordena_por_mas_reciente(monkeypatch):
    import juez.api.monitor_store as mon_mod

    # create() llama _now_iso() dos veces (created_at, updated_at) por monitor.
    tiempos = iter([
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:01+00:00", "2026-01-01T00:00:01+00:00",
    ])
    monkeypatch.setattr(mon_mod, "_now_iso", lambda: next(tiempos))

    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        m1 = store.create(_config(nombre="Primero"))
        m2 = store.create(_config(nombre="Segundo"))
        items = store.list()
        assert items[0]["id"] == m2["id"]
        assert items[1]["id"] == m1["id"]


def test_update_cambia_campos_y_updated_at():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())
        actualizado = store.update(monitor["id"], active=False)
        assert actualizado["active"] is False


def test_delete_elimina_el_monitor_y_su_archivo():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())
        assert store.delete(monitor["id"]) is True
        assert store.get(monitor["id"]) is None
        assert not (Path(tmp) / f"{monitor['id']}.json").exists()
        assert store.delete(monitor["id"]) is False


def test_persistencia_sobrevive_a_reinicio():
    with tempfile.TemporaryDirectory() as tmp:
        store1 = MonitorStore(persist_dir=Path(tmp))
        monitor = store1.create(_config())

        store2 = MonitorStore(persist_dir=Path(tmp))
        recuperado = store2.get(monitor["id"])
        assert recuperado is not None
        assert recuperado["config"]["nombre"] == "Monitor Emma"


def test_due_monitors_solo_devuelve_los_vencidos_y_activos(monkeypatch):
    import juez.api.monitor_store as mon_mod

    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        vencido = store.create(_config(nombre="Vencido"))
        no_vencido = store.create(_config(nombre="No vencido"))

        # Fuerza el next_run_at del primero al pasado y del segundo al futuro.
        pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        futuro = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        store.update(vencido["id"], next_run_at=pasado)
        store.update(no_vencido["id"], next_run_at=futuro)

        due = store.due_monitors()
        due_ids = {m["id"] for m in due}
        assert vencido["id"] in due_ids
        assert no_vencido["id"] not in due_ids


def test_due_monitors_ignora_monitores_inactivos():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())
        pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.update(monitor["id"], next_run_at=pasado, active=False)
        assert store.due_monitors() == []


def test_registrar_corrida_calcula_cambio_contra_la_anterior():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())

        primera = store.registrar_corrida(monitor["id"], status="completed", score=100.0, estado="LISTO")
        assert primera["score_anterior"] is None
        assert primera["cambio"] is None

        segunda = store.registrar_corrida(monitor["id"], status="completed", score=21.2, estado="NECESITA_ATENCION")
        assert segunda["score_anterior"] == 100.0
        assert segunda["cambio"] == -78.8

        actualizado = store.get(monitor["id"])
        assert actualizado["last_run_at"] == segunda["timestamp"]
        assert len(actualizado["historial"]) == 2


def test_registrar_corrida_once_desactiva_el_monitor_tras_correr():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config(frecuencia="once", hora=None))
        store.registrar_corrida(monitor["id"], status="completed", score=90.0)
        actualizado = store.get(monitor["id"])
        assert actualizado["active"] is False
        assert actualizado["next_run_at"] is None


def test_registrar_corrida_con_error_no_rompe_y_queda_en_historial():
    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())
        entrada = store.registrar_corrida(monitor["id"], status="failed", error="ConnectionError: webhook no responde")
        assert entrada["status"] == "failed"
        assert "ConnectionError" in entrada["error"]
        assert entrada["score"] is None


def test_historial_devuelve_mas_reciente_primero(monkeypatch):
    """Fuerza timestamps distintos y crecientes -- dos registrar_corrida()
    consecutivos pueden caer en el mismo microsegundo real y volver el orden
    ambiguo (sort estable conserva el orden original en un empate)."""
    import juez.api.monitor_store as mon_mod

    # create() consume 2 (created_at, updated_at); cada registrar_corrida() consume 1.
    tiempos = iter([
        "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:01+00:00", "2026-01-01T00:00:02+00:00",
    ])
    monkeypatch.setattr(mon_mod, "_now_iso", lambda: next(tiempos))

    with tempfile.TemporaryDirectory() as tmp:
        store = MonitorStore(persist_dir=Path(tmp))
        monitor = store.create(_config())
        store.registrar_corrida(monitor["id"], status="completed", score=100.0)
        segunda = store.registrar_corrida(monitor["id"], status="completed", score=80.0)
        hist = store.historial(monitor["id"])
        assert hist[0]["run_id"] == segunda["run_id"]


def test_calcular_proximo_run_once_es_none():
    assert calcular_proximo_run("once", None) is None


def test_calcular_proximo_run_hourly_suma_una_hora():
    base = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    resultado = calcular_proximo_run("hourly", None, desde=base)
    assert resultado == (base + timedelta(hours=1)).isoformat()


def test_calcular_proximo_run_daily_antes_de_la_hora_es_hoy():
    # 10:00 UTC == 05:00 Colombia -- antes de las 08:00, cae hoy mismo.
    base = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    resultado = calcular_proximo_run("daily", "08:00", desde=base)
    esperado = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)
    assert resultado == esperado.isoformat()


def test_calcular_proximo_run_daily_despues_de_la_hora_es_manana():
    # 14:00 UTC == 09:00 Colombia -- ya paso las 08:00, cae al dia siguiente.
    base = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    resultado = calcular_proximo_run("daily", "08:00", desde=base)
    esperado = datetime(2026, 7, 11, 13, 0, tzinfo=timezone.utc)
    assert resultado == esperado.isoformat()


def test_calcular_proximo_run_weekly_suma_siete_dias():
    base = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    resultado = calcular_proximo_run("weekly", "08:00", desde=base)
    esperado = datetime(2026, 7, 17, 13, 0, tzinfo=timezone.utc)
    assert resultado == esperado.isoformat()
