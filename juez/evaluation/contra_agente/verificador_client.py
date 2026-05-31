"""Cliente HTTP del Verificador para uso desde el Juez en modo e2e.

Encapsula:
  - Healthcheck pre-batch (`GET /health` con timeout corto).
  - Dispatch (`POST /verificador/verify` con source inline/drive + snapshot meta).
  - Polling (`GET /verificador/verify/{id}`) hasta `completed`/`failed`.

Toda excepción HTTP termina en `VerificadorUnavailable` — el batch del Juez
NUNCA debe morir porque el Verificador esté caído. La política operativa
es "degrade gracefully": loggear, marcar el caso como skipped, seguir.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, Optional

import requests

from juez.settings import settings

log = logging.getLogger("juez.verificador_client")


class VerificadorUnavailable(Exception):
    """El verificador no respondió o respondió mal. Caso e2e se marca skipped."""


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.VERIFICADOR_API_KEY:
        h["X-Verifier-Key"] = settings.VERIFICADOR_API_KEY
    return h


def healthcheck(base_url: Optional[str] = None, timeout_s: Optional[float] = None) -> bool:
    """True si `GET {base_url}/health` responde 200 en `timeout_s` segundos."""
    url = (base_url or settings.VERIFICADOR_BASE_URL).rstrip("/") + "/health"
    t = timeout_s if timeout_s is not None else settings.JUEZ_E2E_HEALTH_TIMEOUT_S
    try:
        r = requests.get(url, timeout=t)
        return r.status_code == 200
    except Exception as exc:
        log.warning("verificador healthcheck failed: %s: %s", type(exc).__name__, exc)
        return False


def _verify_dispatch(
    *,
    cliente: str,
    artifact_id: str,
    source: Dict[str, Any],
    expected_snapshot: Dict[str, Any],
    extra_metadata: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    poll_timeout_s: float = 60.0,
    poll_interval_s: float = 1.0,
) -> Dict[str, Any]:
    """Despacha al Verificador y polea hasta tener veredicto. Bloquea.

    `source` debe ser un dict completo con `type` y los campos específicos
    de ese tipo (ej. `{"type": "inline", "blob_base64": "..."}` o
    `{"type": "drive", "file_id": "..."}`).

    Returns:
        Dict con shape de `VerificationResult` del Verificador (status,
        verdict, score, checks, issues, ...).

    Raises:
        VerificadorUnavailable: si el dispatch o el polling fallan.
    """
    base = (base_url or settings.VERIFICADOR_BASE_URL).rstrip("/")
    post_url = base + "/verificador/verify"

    body = {
        "cliente": cliente,
        "artifact_type": "pdf",
        "artifact_id": artifact_id,
        "source": source,
        "metadata": {
            "expected_snapshot": expected_snapshot,
            "synthetic": True,
            **(extra_metadata or {}),
        },
    }

    # ── Dispatch ──────────────────────────────────────────────────────────
    try:
        resp = requests.post(post_url, json=body, headers=_headers(), timeout=10)
    except requests.RequestException as exc:
        raise VerificadorUnavailable(f"POST {post_url}: {type(exc).__name__}: {exc}") from exc

    if resp.status_code not in (200, 202):
        raise VerificadorUnavailable(
            f"POST {post_url} → HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        accepted = resp.json()
    except ValueError as exc:
        raise VerificadorUnavailable(f"Respuesta del verificador no es JSON: {exc}") from exc

    verification_id = accepted.get("verification_id")
    if not verification_id:
        raise VerificadorUnavailable(f"Verificador no devolvió verification_id: {accepted}")

    log.info("verificador.dispatch ok verification_id=%s artifact_id=%s source=%s",
             verification_id, artifact_id, source.get("type"))

    # ── Polling ───────────────────────────────────────────────────────────
    get_url = f"{base}/verificador/verify/{verification_id}"
    t_start = time.time()
    interval = poll_interval_s
    while True:
        if (time.time() - t_start) > poll_timeout_s:
            raise VerificadorUnavailable(
                f"polling timeout ({poll_timeout_s}s) verification_id={verification_id}"
            )
        try:
            r = requests.get(get_url, headers=_headers(), timeout=5)
        except requests.RequestException as exc:
            raise VerificadorUnavailable(
                f"GET {get_url}: {type(exc).__name__}: {exc}"
            ) from exc

        if r.status_code != 200:
            raise VerificadorUnavailable(
                f"GET {get_url} → HTTP {r.status_code}: {r.text[:200]}"
            )

        try:
            result = r.json()
        except ValueError as exc:
            raise VerificadorUnavailable(f"Respuesta del verificador no es JSON: {exc}") from exc

        if result.get("status") in ("completed", "failed"):
            log.info("verificador.done verification_id=%s status=%s verdict=%s score=%s",
                     verification_id, result.get("status"),
                     result.get("verdict"), result.get("score"))
            return result

        # backoff exponencial limitado
        time.sleep(interval)
        interval = min(interval * 1.5, 4.0)


def verify_inline_pdf(
    *,
    cliente: str,
    artifact_id: str,
    pdf_bytes: bytes,
    expected_snapshot: Dict[str, Any],
    extra_metadata: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    poll_timeout_s: float = 60.0,
    poll_interval_s: float = 1.0,
) -> Dict[str, Any]:
    """Variante que sube los bytes del PDF inline (base64) al Verificador."""
    blob_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return _verify_dispatch(
        cliente=cliente,
        artifact_id=artifact_id,
        source={"type": "inline", "blob_base64": blob_b64},
        expected_snapshot=expected_snapshot,
        extra_metadata=extra_metadata,
        base_url=base_url,
        poll_timeout_s=poll_timeout_s,
        poll_interval_s=poll_interval_s,
    )


def verify_drive_pdf(
    *,
    cliente: str,
    artifact_id: str,
    drive_file_id: str,
    expected_snapshot: Dict[str, Any],
    extra_metadata: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    poll_timeout_s: float = 120.0,
    poll_interval_s: float = 1.5,
) -> Dict[str, Any]:
    """Variante de verify_inline_pdf que usa source=drive en vez de inline.

    Útil para auditar PDFs ya generados que están en Google Drive — el
    Verificador se encarga de bajar el blob desde Drive con sus propias
    credenciales. El Juez no necesita acceso a Drive para este flujo.

    Returns:
        Dict con shape de `VerificationResult`.

    Raises:
        VerificadorUnavailable: si el dispatch o el polling fallan.
    """
    return _verify_dispatch(
        cliente=cliente,
        artifact_id=artifact_id,
        source={"type": "drive", "file_id": drive_file_id},
        expected_snapshot=expected_snapshot,
        extra_metadata=extra_metadata,
        base_url=base_url,
        poll_timeout_s=poll_timeout_s,
        poll_interval_s=poll_interval_s,
    )
