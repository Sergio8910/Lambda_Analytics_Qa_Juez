"""Monitores programados: evaluaciones recurrentes sobre uno o varios
agentes, con historial de resultados.

Mismo patron de persistencia que JobStore/ReferenceDataStore: JSON en disco
(outputs/monitors/{id}.json) + cache en memoria, sin Celery/Redis/APScheduler
-- un thread en background revisa cada minuto que monitores ya vencieron
(ver juez/api/scheduler.py).

Antes de esto, "monitoreo programado" (juez/monitoring/) era un script CLI
aislado: sin endpoint HTTP, sin base de datos, sin historial consultable, y
evaluaba con analisis estatico viejo (no la Colmena moderna). Este modulo
reemplaza esa pieza para que el monitoreo corra sobre run_proyecto()
(evaluate_project_path + conversaciones reales) y quede consultable por API.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_PERSIST_DIR = Path(__file__).resolve().parent.parent / "outputs" / "monitors"
_MAX_HISTORIAL_POR_MONITOR = 200

FRECUENCIAS = ("once", "hourly", "daily", "weekly", "monthly")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def calcular_proximo_run(
    frecuencia: str, hora_hhmm: Optional[str], desde: Optional[datetime] = None,
) -> Optional[str]:
    """Calcula el proximo next_run_at (UTC, ISO) a partir de la frecuencia.

    - "once": None (ya se agoto despues de correr una vez).
    - "hourly": +1 hora desde `desde` (o ahora).
    - "daily"/"weekly"/"monthly": proxima ocurrencia de `hora_hhmm` (HH:MM,
      interpretada como hora de Colombia UTC-5) desde `desde` (o ahora).
    """
    base = desde or _now()
    if frecuencia == "once":
        return None
    if frecuencia == "hourly":
        return (base + timedelta(hours=1)).isoformat()

    # daily/weekly/monthly: construir la proxima ocurrencia de HH:MM en
    # hora de Colombia (UTC-5, sin horario de verano).
    try:
        hh, mm = (int(x) for x in (hora_hhmm or "08:00").split(":"))
    except Exception:
        hh, mm = 8, 0
    colombia_offset = timedelta(hours=-5)
    base_col = base + colombia_offset
    candidato_col = base_col.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidato_col <= base_col:
        if frecuencia == "daily":
            candidato_col += timedelta(days=1)
        elif frecuencia == "weekly":
            candidato_col += timedelta(days=7)
        elif frecuencia == "monthly":
            # Aproximacion simple: +30 dias (evita dependencia de calendar
            # para meses de distinta longitud; suficiente para v1).
            candidato_col += timedelta(days=30)
        else:
            candidato_col += timedelta(days=1)
    return (candidato_col - colombia_offset).isoformat()


class MonitorStore:
    """Almacen en memoria + persistencia en disco de monitores programados
    y su historial de corridas."""

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self.persist_dir = Path(persist_dir or _DEFAULT_PERSIST_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._monitors: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load_existing()

    # ── CRUD de monitores ────────────────────────────────────────────────

    def create(self, config: Dict[str, Any]) -> Dict[str, Any]:
        monitor_id = uuid.uuid4().hex[:16]
        frecuencia = config.get("frecuencia", "once")
        monitor = {
            "id": monitor_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "active": True,
            "config": config,
            "last_run_at": None,
            "next_run_at": calcular_proximo_run(frecuencia, config.get("hora")),
            "historial": [],
        }
        with self._lock:
            self._monitors[monitor_id] = monitor
        self._persist(monitor_id)
        return dict(monitor)

    def get(self, monitor_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            m = self._monitors.get(monitor_id)
            return dict(m) if m else None

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._monitors.values())
        items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return [dict(m) for m in items[:limit]]

    def update(self, monitor_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            m = self._monitors.get(monitor_id)
            if not m:
                return None
            m.update(fields)
            m["updated_at"] = _now_iso()
        self._persist(monitor_id)
        return self.get(monitor_id)

    def delete(self, monitor_id: str) -> bool:
        with self._lock:
            existia = self._monitors.pop(monitor_id, None) is not None
        if existia:
            path = self.persist_dir / f"{monitor_id}.json"
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        return existia

    def due_monitors(self) -> List[Dict[str, Any]]:
        """Monitores activos cuyo next_run_at ya paso -- lo que el scheduler
        debe ejecutar en esta pasada."""
        ahora = _now_iso()
        with self._lock:
            items = list(self._monitors.values())
        return [
            dict(m) for m in items
            if m.get("active") and m.get("next_run_at") and m["next_run_at"] <= ahora
        ]

    # ── Historial de corridas ────────────────────────────────────────────

    def registrar_corrida(
        self, monitor_id: str, *, status: str, score: Optional[float] = None,
        estado: Optional[str] = None, resultado: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Agrega una entrada de historial y calcula el delta contra la
        corrida anterior (ANTERIOR/ACTUAL/CAMBIO, igual que el dashboard)."""
        with self._lock:
            m = self._monitors.get(monitor_id)
            if not m:
                return None
            historial = m.setdefault("historial", [])
            anterior_score = historial[-1]["score"] if historial and historial[-1].get("score") is not None else None
            entrada = {
                "run_id": uuid.uuid4().hex[:16],
                "timestamp": _now_iso(),
                "status": status,
                "score_anterior": anterior_score,
                "score": score,
                "cambio": round(score - anterior_score, 1) if score is not None and anterior_score is not None else None,
                "estado": estado,
                "resultado": resultado,
                "error": error,
            }
            historial.append(entrada)
            if len(historial) > _MAX_HISTORIAL_POR_MONITOR:
                del historial[: len(historial) - _MAX_HISTORIAL_POR_MONITOR]
            m["last_run_at"] = entrada["timestamp"]
            frecuencia = m.get("config", {}).get("frecuencia", "once")
            m["next_run_at"] = calcular_proximo_run(frecuencia, m.get("config", {}).get("hora"))
            if frecuencia == "once":
                m["active"] = False
        self._persist(monitor_id)
        return entrada

    def historial(self, monitor_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            m = self._monitors.get(monitor_id)
            if not m:
                return []
            hist = list(m.get("historial", []))
        hist.sort(key=lambda h: h.get("timestamp", ""), reverse=True)
        return hist[:limit]

    # ── Persistencia ─────────────────────────────────────────────────────

    def _persist(self, monitor_id: str) -> None:
        path = self.persist_dir / f"{monitor_id}.json"
        try:
            with self._lock:
                data = self._monitors.get(monitor_id)
                if data is None:
                    return
                payload = json.dumps(data, ensure_ascii=False, default=str, indent=2)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    def _load_existing(self) -> None:
        for path in self.persist_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._monitors[data["id"]] = data
            except Exception:
                continue


_store: Optional[MonitorStore] = None
_store_lock = threading.Lock()


def get_monitor_store() -> MonitorStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MonitorStore()
    return _store
