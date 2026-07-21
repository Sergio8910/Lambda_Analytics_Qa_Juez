"""Almacen persistente de datasets de referencia (informacion real para
pruebas), subir-una-vez / reusar-en-muchas-corridas.

Mismo patron que JobStore (juez/api/jobs.py): sin Celery ni Redis, archivos
JSON en disco (outputs/reference_data/{id}.json) + cache en memoria. Antes de
esto, POST /reference-data/ingest parseaba y descartaba -- no habia forma de
subir un dataset una vez y reusarlo en corridas/monitores futuros.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from juez.evaluation.reference_data.models import ReferenceDataset

_DEFAULT_PERSIST_DIR = Path(__file__).resolve().parent.parent / "outputs" / "reference_data"
_RETENTION_DAYS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReferenceDataStore:
    """Almacen en memoria + persistencia en disco de ReferenceDataset por id."""

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self.persist_dir = Path(persist_dir or _DEFAULT_PERSIST_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load_existing()

    def save(self, dataset: ReferenceDataset) -> Dict[str, Any]:
        """Persiste un dataset ya parseado y devuelve su entrada (incluye 'id')."""
        dataset_id = uuid.uuid4().hex[:16]
        entry = {
            "id": dataset_id,
            "created_at": _now_iso(),
            "dataset": dataset.model_dump(mode="json"),
        }
        with self._lock:
            self._entries[dataset_id] = entry
        self._persist(dataset_id)
        return entry

    def get(self, dataset_id: str) -> Optional[ReferenceDataset]:
        with self._lock:
            entry = self._entries.get(dataset_id)
        if not entry:
            return None
        return ReferenceDataset(**entry["dataset"])

    def get_entry(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(dataset_id)
            return dict(entry) if entry else None

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            entries = list(self._entries.values())
        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return [
            {"id": e["id"], "created_at": e["created_at"], "resumen": ReferenceDataset(**e["dataset"]).resumen()}
            for e in entries[:limit]
        ]

    def delete(self, dataset_id: str) -> bool:
        """Elimina un dataset (memoria + disco). True si existía."""
        with self._lock:
            existia = self._entries.pop(dataset_id, None) is not None
        if existia:
            try:
                (self.persist_dir / f"{dataset_id}.json").unlink(missing_ok=True)
            except Exception:
                pass
        return existia

    def _persist(self, dataset_id: str) -> None:
        path = self.persist_dir / f"{dataset_id}.json"
        try:
            with self._lock:
                data = self._entries.get(dataset_id)
                if data is None:
                    return
                payload = json.dumps(data, ensure_ascii=False, default=str, indent=2)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass  # no queremos que un fallo de disco tumbe el ingest

    def _load_existing(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
        for path in self.persist_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(data.get("created_at", "").replace("Z", "+00:00"))
                if created < cutoff:
                    continue
                self._entries[data["id"]] = data
            except Exception:
                continue


_store: Optional[ReferenceDataStore] = None
_store_lock = threading.Lock()


def get_reference_store() -> ReferenceDataStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ReferenceDataStore()
    return _store
