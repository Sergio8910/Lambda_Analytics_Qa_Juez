"""Protocolo de un adapter de cliente.

Un cliente sabe traducir un `artifact_id` (algo que tiene sentido en su
mundo, ej. `INV-789` para Abad) a un `ExpectedSnapshot` (lo que el
artefacto debería contener).

Toda implementación DEBE ser estrictamente read-only sobre la BD productiva
del cliente.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

from ..schemas import ExpectedSnapshot


class ClientError(Exception):
    """Base de errores de cliente. UNVERIFIABLE en el verifier."""


class ClientNotFoundError(ClientError):
    """El artifact_id no existe en la BD del cliente."""


class ClientDBError(ClientError):
    """Error de BD: conexión caída, timeout, query inválido, etc."""


@runtime_checkable
class BaseClientAdapter(Protocol):
    """Un adapter de cliente sabe construir el esperado de un artefacto."""

    name: str

    def fetch_expected(
        self,
        artifact_id: str,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> ExpectedSnapshot:
        """Retorna lo que el cliente dice que el artefacto debería contener.

        `request_metadata` es el campo `metadata` del request HTTP. Los
        clientes con BD propia (ej. `abad`) lo ignoran. Los clientes
        sintéticos (ej. `abad_synthetic`) lo usan para extraer el snapshot
        que el productor del artefacto envió inline.

        Para clientes con BD productiva:
          - DEBE abrir conexión con `SET TRANSACTION READ ONLY`.
          - DEBE usar statement_timeout corto (configurable vía settings).
          - DEBE limitar conexiones concurrentes para no saturar la BD del cliente.
          - DEBE lanzar `ClientNotFoundError` si el artifact_id no aparece.
          - DEBE lanzar `ClientDBError` ante cualquier problema de BD; NUNCA
            reintentar agresivamente.
        """
        ...
