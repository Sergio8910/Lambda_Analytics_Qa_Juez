"""Política de retention del Verificador.

Borra filas viejas de la tabla `verifications`. Aditivo: no toca el resto
del storage. Se puede invocar desde el CLI (`scripts/cleanup_old_verifications.py`)
o desde un scheduler externo (cron).

Decisiones de diseño:
  - Comparación contra `created_at` (no `completed_at`) — incluye trabajos
    que quedaron en `queued`/`running`/`failed` sin completar.
  - Devuelve `oldest_kept_at` para que el caller pueda loguear/alertar si
    la BD se está vaciando demasiado.
  - `dry_run=True` retorna el conteo de filas que serían borradas, sin
    ejecutar el DELETE. Esencial para validar antes de correr en prod.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import delete, func, select

from .storage import SessionLocal, Verification

log = logging.getLogger("verificador.retention")


def purge_old_verifications(days: int = 90, dry_run: bool = False) -> Dict[str, Any]:
    """Borra (o cuenta) verifications con `created_at < now() - days`.

    Args:
        days: número de días a retener. `days=0` borra todo (útil para tests).
              Valores negativos se tratan como 0.
        dry_run: si True, solo cuenta filas afectadas sin borrar nada.

    Returns:
        dict con:
          - `deleted_count`: int — filas borradas (o que serían borradas si dry_run).
          - `oldest_kept_at`: str ISO 8601 — fecha de la fila más antigua que
            queda en la tabla después del purge (o None si la tabla quedó vacía).
          - `dry_run`: bool — espejo del input para confirmación.
    """
    if days < 0:
        days = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as session:
        # Conteo de filas que serían (o son) afectadas.
        count_stmt = (
            select(func.count())
            .select_from(Verification)
            .where(Verification.created_at < cutoff)
        )
        deleted_count = int(session.execute(count_stmt).scalar_one() or 0)

        if not dry_run and deleted_count > 0:
            session.execute(
                delete(Verification).where(Verification.created_at < cutoff)
            )
            session.commit()

        # La fila más vieja que queda — None si la tabla está vacía.
        oldest_stmt = select(func.min(Verification.created_at))
        oldest_dt = session.execute(oldest_stmt).scalar_one_or_none()

    if oldest_dt is not None:
        # SQLite puede devolver datetime naive; normalizamos a UTC para ISO.
        if oldest_dt.tzinfo is None:
            oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
        oldest_kept_at = oldest_dt.isoformat()
    else:
        oldest_kept_at = None

    result: Dict[str, Any] = {
        "deleted_count": deleted_count,
        "oldest_kept_at": oldest_kept_at,
        "dry_run": dry_run,
    }

    log.info(
        "retention.purge days=%d dry_run=%s deleted_count=%d oldest_kept_at=%s cutoff=%s",
        days,
        dry_run,
        deleted_count,
        oldest_kept_at,
        cutoff.isoformat(),
    )
    return result
