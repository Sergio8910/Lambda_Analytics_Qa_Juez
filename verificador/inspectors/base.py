"""Protocolo de un inspector de artefacto.

Un inspector sabe (a) parsear un tipo de artefacto (PDF, imagen, etc.) y
(b) compararlo contra un `ExpectedSnapshot`. Retorna un `InspectorReport`
con los checks individuales y un veredicto agregado.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas import ExpectedSnapshot, InspectorReport


class InspectorError(Exception):
    """Errores del inspector — generalmente UNVERIFIABLE."""


@runtime_checkable
class BaseInspector(Protocol):
    """Un inspector audita un artefacto contra lo esperado."""

    artifact_type: str

    def inspect(self, blob: bytes, expected: ExpectedSnapshot) -> InspectorReport:
        """Audita el artefacto y retorna un reporte estructurado.

        - DEBE manejar el blob solo en memoria — NUNCA persistirlo.
        - DEBE atrapar errores de parsing (PDF encriptado, formato inválido)
          y marcarlos como `UNVERIFIABLE` con razón clara.
        - NO loggea contenido del artefacto, solo IDs y conteos.
        """
        ...
