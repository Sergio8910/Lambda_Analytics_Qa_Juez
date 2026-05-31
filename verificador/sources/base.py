"""Protocolo de una fuente de descarga de artefactos.

Una fuente sabe descargar un blob dado un "source spec" (un dict con
metadatos: file_id para Drive, key para S3, url, etc.). Es agnóstica al
tipo de artefacto — solo descarga bytes.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


class SourceError(Exception):
    """Base de errores de fuente. UNVERIFIABLE en el verifier."""


class SourceNotFoundError(SourceError):
    """El archivo no existe (404)."""


class SourceAuthError(SourceError):
    """Token expirado o sin permisos (401/403)."""


class SourceTimeoutError(SourceError):
    """Timeout (incluye reintentos agotados por eventual consistency)."""


@runtime_checkable
class BaseSource(Protocol):
    """Una fuente sabe descargar un artefacto a partir de un spec."""

    type_name: str

    def fetch(self, spec: Mapping[str, Any]) -> bytes:
        """Descarga el artefacto y retorna los bytes.

        Debe lanzar `SourceNotFoundError`, `SourceAuthError`, `SourceTimeoutError`
        o `SourceError` según el caso. Cualquier excepción no-SourceError es
        un bug y se propaga.

        IMPORTANTE: no debe loggear el contenido del blob ni la URL completa
        con tokens. Solo IDs ofuscados y conteos.
        """
        ...
