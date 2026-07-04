"""Descarga de archivos desde Google Drive.

Estrategia: HTTP GET directo al endpoint de Drive con `Authorization: Bearer`.
Patrón lifted de `evaluation/artifact/evaluators/pdf_db.py:59-71` del Juez,
adaptado para retornar bytes y manejar reintentos por eventual consistency.

NO descarga si el archivo excede `MAX_ARTIFACT_BYTES` (default 50MB).
NUNCA loggea el token completo ni el contenido del archivo descargado.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import requests

from ..settings import settings
from . import register_source
from .base import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceTimeoutError,
)

log = logging.getLogger("verificador.sources.drive")

_DRIVE_FILE_URL = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"


def _redact(token: str) -> str:
    """Reduce el token a un fingerprint corto para logs (nunca el valor completo)."""
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "<short>"
    return f"{token[:4]}...{token[-4:]}"


class DriveSource:
    """Implementa `BaseSource` para Google Drive vía REST API.

    Reintentos: 3 intentos con backoff exponencial (3s, 9s, 27s por default).
    Eventual consistency en Drive es común cuando n8n acaba de subir un PDF
    y el verificador intenta bajarlo a los pocos segundos.

    Códigos HTTP que se mapean a excepciones:
      - 404         -> SourceNotFoundError (no reintenta — no va a aparecer)
      - 401/403     -> SourceAuthError (no reintenta — token roto)
      - 5xx, 429    -> reintenta hasta el cap
      - timeout     -> reintenta hasta el cap
      - otros       -> SourceError
    """

    type_name = "drive"

    def __init__(
        self,
        token: str | None = None,
        timeout_s: int | None = None,
        retry_max: int | None = None,
        retry_base_delay_s: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.token = token or settings.GOOGLE_OAUTH_TOKEN
        self.timeout_s = timeout_s if timeout_s is not None else settings.DRIVE_TIMEOUT_S
        self.retry_max = retry_max if retry_max is not None else settings.DRIVE_RETRY_MAX
        self.retry_base_delay_s = (
            retry_base_delay_s if retry_base_delay_s is not None else settings.DRIVE_RETRY_DELAY_S
        )
        self.max_bytes = max_bytes if max_bytes is not None else settings.MAX_ARTIFACT_BYTES

    def fetch(self, spec: Mapping[str, Any]) -> bytes:
        file_id = spec.get("file_id")
        if not file_id:
            raise SourceError("Drive source spec requiere 'file_id'")
        if not self.token:
            raise SourceAuthError("GOOGLE_OAUTH_TOKEN no configurado")

        url = _DRIVE_FILE_URL.format(file_id=file_id)
        headers = {"Authorization": f"Bearer {self.token}"}

        # Reintentos con backoff exponencial. NO reintentar errores definitivos
        # (404, 401, 403) — solo transientes (5xx, 429, timeout, conexión).
        last_exc: Exception | None = None
        for intento in range(self.retry_max):
            try:
                return self._do_fetch(url, headers, file_id)
            except (SourceNotFoundError, SourceAuthError):
                # Fail-fast: estos no se arreglan reintentando
                raise
            except (SourceTimeoutError, SourceError) as exc:
                last_exc = exc
                if intento + 1 < self.retry_max:
                    delay = self.retry_base_delay_s * (3 ** intento)
                    log.warning(
                        "drive_fetch reintenta file_id=%s intento=%d/%d delay_s=%d motivo=%s",
                        file_id, intento + 1, self.retry_max, delay, type(exc).__name__,
                    )
                    time.sleep(delay)
                    continue
        # Si llegó acá agotó todos los reintentos
        raise SourceTimeoutError(
            f"Drive no respondió tras {self.retry_max} intentos: {last_exc}"
        ) from last_exc

    def _do_fetch(self, url: str, headers: Mapping[str, str], file_id: str) -> bytes:
        try:
            with requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=self.timeout_s,
                allow_redirects=True,
            ) as resp:
                status = resp.status_code
                if status == 404:
                    raise SourceNotFoundError(f"Archivo no existe en Drive: file_id={file_id}")
                if status in (401, 403):
                    raise SourceAuthError(
                        f"Drive rechazó autenticación (HTTP {status}). "
                        f"Token={_redact(self.token or '')} expirado o sin permisos."
                    )
                if status == 429:
                    raise SourceError(f"Drive rate limit (429) para file_id={file_id}")
                if status >= 500:
                    raise SourceError(f"Drive error {status} para file_id={file_id}")
                resp.raise_for_status()

                # Stream + cap de tamaño para no agotar memoria si el archivo es enorme
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise SourceError(
                            f"Archivo excede MAX_ARTIFACT_BYTES ({self.max_bytes}) — abortado a {total} bytes"
                        )
                    chunks.append(chunk)
                blob = b"".join(chunks)
                log.info(
                    "drive_fetch ok file_id=%s bytes=%d",
                    file_id, len(blob),
                )
                return blob
        except requests.exceptions.Timeout as exc:
            raise SourceTimeoutError(f"Timeout en Drive ({self.timeout_s}s) para file_id={file_id}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise SourceError(f"Error de conexión a Drive para file_id={file_id}: {exc}") from exc
        except (SourceError, SourceNotFoundError, SourceAuthError, SourceTimeoutError):
            raise
        except Exception as exc:
            raise SourceError(f"Error inesperado en Drive: {type(exc).__name__}: {exc}") from exc


# Auto-registro
register_source("drive", DriveSource)
