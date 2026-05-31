"""Source 'inline' — bytes del artefacto vienen en el request HTTP en base64.

Útil para flujos sintéticos: el Juez en modo e2e sintético construye un PDF en
memoria y se lo manda al Verificador en el mismo request, sin pasar por Drive.

NUNCA se persiste el blob. Vive solo durante el ciclo de la verificación.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Mapping

from . import register_source
from .base import SourceError

log = logging.getLogger("verificador.sources.inline")


class InlineSource:
    """Implementa `BaseSource`. Decodifica `blob_base64` y retorna los bytes."""

    type_name = "inline"

    def fetch(self, spec: Mapping[str, Any]) -> bytes:
        b64 = spec.get("blob_base64")
        if not b64:
            raise SourceError("Inline source requiere 'blob_base64'")
        try:
            blob = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise SourceError(f"blob_base64 inválido: {exc}") from exc
        if not blob:
            raise SourceError("blob_base64 decodificado está vacío")
        log.info("inline_fetch ok bytes=%d", len(blob))
        return blob


# Auto-registro
register_source("inline", InlineSource)
