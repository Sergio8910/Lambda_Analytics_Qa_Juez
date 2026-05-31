"""Gestor de jobs asincrónicos para la API del Juez.

Cada job se ejecuta en un thread daemon y se persiste a disco en
`outputs/api_jobs/{job_id}.json` para sobrevivir reinicios del servidor.

No usa Celery ni Redis: para los volúmenes esperados (decenas de
evaluaciones por día), threads + archivos son suficientes y simplifican
mucho el despliegue.
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# Directorio de persistencia (relativo al root del proyecto)
_DEFAULT_PERSIST_DIR = Path(__file__).resolve().parent.parent / "outputs" / "api_jobs"

# Retención: jobs más viejos que esto se ignoran al cargar
_JOB_RETENTION_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Almacén de jobs en memoria + persistencia en disco."""

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self.persist_dir = Path(persist_dir or _DEFAULT_PERSIST_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load_existing()

    # ──────────────────────────────────────────────────────────────────────
    # API pública
    # ──────────────────────────────────────────────────────────────────────

    def create(self, kind: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Crea un nuevo job en estado 'queued' y lo persiste."""
        job_id = uuid.uuid4().hex[:16]
        job = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "created_at": _now_iso(),
            "started_at": None,
            "completed_at": None,
            "progress": None,
            "result": None,
            "error": None,
            "params": _scrub_params(params or {}),
        }
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job_id)
        return job

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retorna el job o None si no existe."""
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit: int = 50, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista los jobs más recientes."""
        with self._lock:
            jobs = list(self._jobs.values())
        if kind:
            jobs = [j for j in jobs if j.get("kind") == kind]
        jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return [dict(j) for j in jobs[:limit]]

    def update(self, job_id: str, **fields: Any) -> None:
        """Actualiza campos de un job y persiste."""
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(fields)
        self._persist(job_id)

    def set_progress(self, job_id: str, step: str, percent: int = 0) -> None:
        """Atajo para actualizar el progreso."""
        self.update(job_id, progress={"step": step, "percent": int(percent)})

    def run_in_thread(
        self,
        job_id: str,
        fn: Callable[..., Dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Lanza la función en un thread daemon y maneja transiciones de estado.

        La función recibe automáticamente un kwarg extra `progress_cb` que se
        usa para reportar el progreso desde dentro.
        """

        def _progress_cb(step: str, percent: int = 0) -> None:
            self.set_progress(job_id, step, percent)

        def _wrapper() -> None:
            self.update(job_id, status="running", started_at=_now_iso())
            try:
                result = fn(*args, progress_cb=_progress_cb, **kwargs)
                self.update(
                    job_id,
                    status="completed",
                    result=result,
                    progress={"step": "Completado", "percent": 100},
                    completed_at=_now_iso(),
                )
            except Exception as exc:
                self.update(
                    job_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                    completed_at=_now_iso(),
                )

        thread = threading.Thread(target=_wrapper, daemon=True, name=f"job-{job_id}")
        thread.start()

    # ──────────────────────────────────────────────────────────────────────
    # Persistencia
    # ──────────────────────────────────────────────────────────────────────

    def _persist(self, job_id: str) -> None:
        """Escribe el job a disco."""
        path = self.persist_dir / f"{job_id}.json"
        try:
            with self._lock:
                data = self._jobs.get(job_id)
                if data is None:
                    return
                payload = json.dumps(data, ensure_ascii=False, default=str, indent=2)
            # Escritura atómica: tmp + rename
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            # No queremos que un fallo de disco mate el evaluador
            pass

    def _load_existing(self) -> None:
        """Carga jobs existentes al arrancar (solo los recientes)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=_JOB_RETENTION_DAYS)
        for path in self.persist_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(data.get("created_at", "").replace("Z", "+00:00"))
                if created < cutoff:
                    continue
                # Jobs que estaban "running" cuando el servidor murió: marcarlos fallidos
                if data.get("status") in ("queued", "running"):
                    data["status"] = "failed"
                    data["error"] = "El servidor se reinició antes de que el job terminara."
                    data["completed_at"] = _now_iso()
                self._jobs[data["job_id"]] = data
            except Exception:
                continue


def _scrub_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Quita claves sensibles antes de persistir (API keys, etc.)."""
    SENSITIVE = {"openai_key", "elevenlabs_key", "n8n_api_key", "api_key"}
    out: Dict[str, Any] = {}
    for k, v in params.items():
        if k.lower() in SENSITIVE and v:
            out[k] = "***redacted***"
        elif isinstance(v, dict):
            out[k] = _scrub_params(v)
        else:
            out[k] = v
    return out


# ──────────────────────────────────────────────────────────────────────────
# Singleton global del store
# ──────────────────────────────────────────────────────────────────────────

_store: Optional[JobStore] = None
_store_lock = threading.Lock()


def get_store() -> JobStore:
    """Retorna el JobStore singleton (lazy)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = JobStore()
    return _store
