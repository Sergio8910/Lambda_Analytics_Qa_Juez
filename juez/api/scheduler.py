"""Scheduler de monitores programados: un thread en background que revisa
periodicamente que monitores ya vencieron y los ejecuta.

Sin APScheduler/Celery a proposito -- el servidor se empaqueta como un
ejecutable compilado (service/JuezApi.exe) y agregar una dependencia nueva
implica rehacer ese empaquetado. Un thread + polling es suficiente para el
volumen esperado (monitores por minuto/hora, no miles por segundo) y sigue
el mismo principio ya usado en JobStore ("threads + archivos, sin Celery ni
Redis").
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Dict, Optional

from juez.api.monitor_store import get_monitor_store

logger = logging.getLogger("juez.scheduler")

_INTERVALO_POLL_S = 30


def _componentes_desde_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Traduce la config guardada del monitor a los kwargs de run_proyecto()."""
    return {
        "nombre": config.get("nombre", "Monitor"),
        "prompt": config.get("prompt", ""),
        "eleven_ids": config.get("eleven_ids") or [],
        "n8n_flows": config.get("n8n_flows") or [],
        "total_conversaciones": config.get("total_conversaciones", 10),
        "concurrencia": config.get("concurrencia", 3),
        "escenarios": config.get("escenarios") or [],
        "incluir_conversaciones": config.get("incluir_conversaciones", True),
        "incluir_dinamicas": config.get("incluir_dinamicas", False),
        "modo_ejecucion": config.get("modo_ejecucion", "sandbox"),
        "reglas_negocio": config.get("reglas_negocio") or [],
        "objetivos": config.get("objetivos"),
        "reference_dataset_id": config.get("reference_dataset_id"),
        "openai_key": config.get("openai_key") or "",
        "elevenlabs_key": config.get("elevenlabs_key") or "",
        "n8n_api_key": config.get("n8n_api_key") or "",
        "n8n_base_url": config.get("n8n_base_url") or "",
    }


def ejecutar_monitor(monitor: Dict[str, Any]) -> Dict[str, Any]:
    """Corre un monitor UNA vez (via run_proyecto, la Colmena moderna) y
    registra el resultado en su historial. Nunca lanza -- un monitor que
    falla se registra con status='failed', no tumba el scheduler."""
    from juez.api.runner import run_proyecto

    store = get_monitor_store()
    monitor_id = monitor["id"]
    config = monitor.get("config", {})
    try:
        resultado = run_proyecto(**_componentes_desde_config(config))
        entrada = store.registrar_corrida(
            monitor_id, status="completed",
            score=resultado.get("score"), estado=resultado.get("estado"),
            resultado=resultado,
        )
    except Exception as exc:
        logger.warning("Monitor %s fallo: %s", monitor_id, exc)
        entrada = store.registrar_corrida(
            monitor_id, status="failed",
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )
    return entrada or {}


class MonitorScheduler:
    """Thread daemon que revisa cada `_INTERVALO_POLL_S` segundos los
    monitores vencidos y los ejecuta (cada uno en su propio thread, para que
    uno lento no bloquee la deteccion de los demas)."""

    def __init__(self, intervalo_s: int = _INTERVALO_POLL_S) -> None:
        self.intervalo_s = intervalo_s
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="monitor-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> Dict[str, Any]:
        """Salud del scheduler para observabilidad: si el thread vive, cada
        cuánto revisa, cuántos monitores activos hay, cuáles están vencidos
        ahora, sus próximas corridas y la última corrida/error de cada uno."""
        store = get_monitor_store()
        monitores = store.list(limit=500)
        activos = [m for m in monitores if m.get("active")]
        try:
            due = store.due_monitors()
        except Exception:
            due = []
        proximas = sorted(
            (
                {
                    "id": m["id"],
                    "nombre": m.get("config", {}).get("nombre", ""),
                    "next_run_at": m.get("next_run_at"),
                    "last_run_at": m.get("last_run_at"),
                }
                for m in activos
                if m.get("next_run_at")
            ),
            key=lambda x: x["next_run_at"] or "",
        )[:20]
        errores = []
        for m in monitores:
            hist = m.get("historial") or []
            if hist and hist[-1].get("status") == "failed":
                errores.append({
                    "id": m["id"],
                    "nombre": m.get("config", {}).get("nombre", ""),
                    "error": str(hist[-1].get("error", ""))[:200],
                    "timestamp": hist[-1].get("timestamp"),
                })
        return {
            "corriendo": bool(self._thread and self._thread.is_alive()),
            "intervalo_poll_s": self.intervalo_s,
            "monitores_totales": len(monitores),
            "monitores_activos": len(activos),
            "vencidos_ahora": [m["id"] for m in due],
            "proximas_corridas": proximas,
            "errores_recientes": errores[:20],
        }

    def _loop(self) -> None:
        store = get_monitor_store()
        while not self._stop.is_set():
            try:
                for monitor in store.due_monitors():
                    threading.Thread(
                        target=ejecutar_monitor, args=(monitor,), daemon=True,
                        name=f"monitor-run-{monitor['id']}",
                    ).start()
            except Exception:
                logger.exception("Error en el ciclo del scheduler de monitores")
            self._stop.wait(self.intervalo_s)


_scheduler: Optional[MonitorScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> MonitorScheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = MonitorScheduler()
    return _scheduler


def start_scheduler() -> None:
    """Arranca el scheduler global. Se llama una vez al iniciar el servidor
    (juez/api/main.py). Idempotente -- llamarlo varias veces no crea threads
    duplicados."""
    get_scheduler().start()
