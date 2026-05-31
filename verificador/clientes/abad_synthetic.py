"""Cliente 'abad_synthetic' — variante sintética de Abad sin tocar Postgres.

Diseñado para el modo e2e sintético del Juez: el `ExpectedSnapshot` viene en
el campo `metadata.expected_snapshot` del request HTTP en vez de leerse de
la BD productiva.

Garantía operativa: cero queries a `abad_faciolince`. Cero credenciales de BD
del cliente involucradas. Mismo modelo de validación que el cliente Abad real.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..schemas import ExpectedSnapshot
from . import register_client
from .base import ClientError, ClientNotFoundError

log = logging.getLogger("verificador.clientes.abad_synthetic")


class AbadSyntheticClient:
    """Implementa `BaseClientAdapter` leyendo el snapshot desde request metadata."""

    name = "abad_synthetic"

    def fetch_expected(
        self,
        artifact_id: str,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> ExpectedSnapshot:
        meta = request_metadata or {}
        snapshot_raw = meta.get("expected_snapshot")
        if not snapshot_raw:
            raise ClientNotFoundError(
                "abad_synthetic: el request no incluyó 'expected_snapshot' en metadata"
            )
        try:
            snapshot = ExpectedSnapshot(**snapshot_raw)
        except Exception as exc:
            raise ClientError(
                f"abad_synthetic: expected_snapshot inválido: {type(exc).__name__}: {exc}"
            ) from exc
        # El artifact_id del request manda; el del snapshot es informativo.
        if snapshot.artifact_id != artifact_id:
            log.warning(
                "abad_synthetic: artifact_id del snapshot (%s) != del request (%s) — "
                "usando el del request",
                snapshot.artifact_id, artifact_id,
            )
            snapshot.artifact_id = artifact_id
        log.info(
            "abad_synthetic.fetch_expected artifact_id=%s ambientes=%d fotos=%d",
            artifact_id,
            len(snapshot.structure.get("ambientes") or []),
            snapshot.counts.get("fotos", 0),
        )
        return snapshot


# Auto-registro
register_client("abad_synthetic", AbadSyntheticClient)
