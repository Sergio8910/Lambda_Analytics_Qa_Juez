"""Orquestador de una verificación.

Resuelve componentes por nombre (cliente / source / inspector) y los
ejecuta en orden. Actualiza estado en `storage` en cada hito. Diseñado
para correr en un daemon thread vía `jobs.run_in_thread`.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from . import storage
from .clientes import get_client
from .clientes.base import ClientError, ClientNotFoundError
from .inspectors import get_inspector
from .inspectors.base import InspectorError
from .schemas import (
    Issue,
    Severidad,
    VerifyWebhookRequest,
    Verdict,
)
from .sources import get_source
from .sources.base import SourceAuthError, SourceError, SourceNotFoundError, SourceTimeoutError

log = logging.getLogger("verificador.verifier")


def _unverifiable(
    verification_id: str,
    razon: str,
    skip_reason: str,
    elapsed_ms: int,
    expected_dump: Optional[Dict[str, Any]] = None,
) -> None:
    """Persiste el resultado UNVERIFIABLE de manera consistente."""
    issues = [Issue(
        severidad=Severidad.INFO,
        mensaje=razon,
        check="setup",
        detalles={"skip_reason": skip_reason},
    )]
    storage.mark_completed(
        verification_id=verification_id,
        verdict=Verdict.UNVERIFIABLE,
        score=0.0,
        checks=[],
        issues=issues,
        expected_snapshot=expected_dump or {},
        elapsed_ms=elapsed_ms,
    )


def run_verification(verification_id: str, payload: VerifyWebhookRequest) -> None:
    """Pipeline completo. Diseñada para correr en thread; actualiza BD a medida."""
    t0 = time.time()
    storage.mark_running(verification_id)
    log.info(
        "verifier.start verification_id=%s cliente=%s artifact_type=%s artifact_id=%s",
        verification_id, payload.cliente, payload.artifact_type, payload.artifact_id,
    )

    expected_dump: Dict[str, Any] = {}

    try:
        # ── 1. Resolver cliente y obtener "lo esperado" ──────────────────
        try:
            client = get_client(payload.cliente)
        except KeyError as exc:
            _unverifiable(
                verification_id,
                f"Cliente '{payload.cliente}' no registrado: {exc}",
                "client_not_registered",
                int((time.time() - t0) * 1000),
            )
            return

        try:
            expected = client.fetch_expected(
                payload.artifact_id,
                request_metadata=payload.metadata,
            )
            expected_dump = expected.model_dump(mode="json")
        except ClientNotFoundError as exc:
            _unverifiable(
                verification_id,
                f"Artifact '{payload.artifact_id}' no encontrado en BD del cliente: {exc}",
                "artifact_not_in_client_db",
                int((time.time() - t0) * 1000),
            )
            return
        except ClientError as exc:
            _unverifiable(
                verification_id,
                f"Error de BD del cliente: {exc}",
                "client_db_error",
                int((time.time() - t0) * 1000),
            )
            return

        # ── 2. Resolver source y descargar el artefacto ──────────────────
        try:
            source = get_source(payload.source.type)
        except KeyError as exc:
            _unverifiable(
                verification_id,
                f"Source '{payload.source.type}' no registrado: {exc}",
                "source_not_registered",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return

        source_spec = payload.source.model_dump()
        # type es solo discriminador, no parte del spec real
        source_spec.pop("type", None)

        try:
            blob = source.fetch(source_spec)
        except SourceNotFoundError as exc:
            _unverifiable(
                verification_id,
                f"Artefacto no encontrado en la fuente: {exc}",
                "artifact_not_in_source",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return
        except SourceAuthError as exc:
            _unverifiable(
                verification_id,
                f"Auth con la fuente falló: {exc}",
                "source_auth_error",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return
        except SourceTimeoutError as exc:
            _unverifiable(
                verification_id,
                f"Timeout en la fuente tras reintentos: {exc}",
                "source_timeout",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return
        except SourceError as exc:
            _unverifiable(
                verification_id,
                f"Error con la fuente: {exc}",
                "source_error",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return

        artifact_size = len(blob)

        # ── 3. Resolver inspector y auditar ──────────────────────────────
        try:
            inspector = get_inspector(payload.artifact_type)
        except KeyError as exc:
            _unverifiable(
                verification_id,
                f"Inspector para '{payload.artifact_type}' no registrado: {exc}",
                "inspector_not_registered",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return

        try:
            report = inspector.inspect(blob, expected)
        except InspectorError as exc:
            _unverifiable(
                verification_id,
                f"Inspector no pudo procesar el artefacto: {exc}",
                "inspector_error",
                int((time.time() - t0) * 1000),
                expected_dump,
            )
            return

        # ── 4. Persistir resultado ───────────────────────────────────────
        elapsed = int((time.time() - t0) * 1000)
        # Issues globales = aplanar issues de cada check + issues sueltos del report
        all_issues = [i for c in report.checks for i in c.issues]
        storage.mark_completed(
            verification_id=verification_id,
            verdict=report.overall_verdict,
            score=report.overall_score,
            checks=report.checks,
            issues=all_issues,
            expected_snapshot=expected_dump,
            artifact_size_bytes=artifact_size,
            elapsed_ms=elapsed,
        )
        log.info(
            "verifier.done verification_id=%s verdict=%s score=%.3f elapsed_ms=%d",
            verification_id, report.overall_verdict.value, report.overall_score, elapsed,
        )

    except Exception as exc:  # noqa: BLE001 — captura final defensiva
        log.exception("verifier.crash verification_id=%s", verification_id)
        storage.mark_failed(
            verification_id=verification_id,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=int((time.time() - t0) * 1000),
        )
