"""Router HTTP del Verificador.

Endpoints:
    POST   /verify                  - dispara una verificación async (202)
    GET    /verify/{verification_id} - estado/resultado de una verificación
    GET    /health                   - probe simple (definido en app.py)

Auth: header `X-Verifier-Key` debe coincidir con `VERIFICADOR_API_KEY`.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status

from . import jobs, storage, verifier
from .schemas import (
    VerificationResult,
    VerificationStatus,
    VerifyAcceptedResponse,
    VerifyWebhookRequest,
)
from .settings import settings

log = logging.getLogger("verificador.router")

router = APIRouter(prefix="/verificador", tags=["verificador"])


# ─────────────────────────────────────────────────────────────────────────────
# Auth dependency
# ─────────────────────────────────────────────────────────────────────────────

def require_api_key(x_verifier_key: Optional[str] = Header(None)) -> None:
    """Valida el header `X-Verifier-Key` contra `VERIFICADOR_API_KEY`.

    Usa `secrets.compare_digest` para evitar timing attacks.
    """
    expected = settings.VERIFICADOR_API_KEY
    if not expected:
        # El servicio arranca sin API key configurada → todo request es rechazado
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VERIFICADOR_API_KEY no está configurado en el servidor",
        )
    if not x_verifier_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header X-Verifier-Key",
        )
    if not secrets.compare_digest(x_verifier_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Verifier-Key inválido",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/verify",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=VerifyAcceptedResponse,
    dependencies=[Depends(require_api_key)],
)
async def verify(payload: VerifyWebhookRequest) -> VerifyAcceptedResponse:
    """Encola una verificación.

    Idempotencia: si ya existe una verificación para
    `(cliente, artifact_id)`, retorna esa misma (no crea duplicado).
    """
    # Idempotencia: si ya hay una verificación queued/running/completed, devolvemos
    # esa. Si está failed y han pasado >5min, permitimos retry creando una nueva.
    existing = storage.get_by_artifact(payload.cliente, payload.artifact_id)
    if existing is not None and existing.status != VerificationStatus.FAILED:
        return VerifyAcceptedResponse(
            verification_id=existing.verification_id,
            status=existing.status,
            poll_url=f"/verificador/verify/{existing.verification_id}",
        )

    # Crear nueva (o devolver la idempotente si hay race)
    verification_id = storage.create_pending(
        cliente=payload.cliente,
        artifact_type=payload.artifact_type,
        artifact_id=payload.artifact_id,
        source_type=payload.source.type,
        source_ref=payload.source.model_dump(),
        extra_metadata=payload.metadata,
    )

    # Lanzar la verificación en background
    jobs.run_in_thread(verifier.run_verification, verification_id, payload)

    log.info(
        "verify.queued verification_id=%s cliente=%s artifact_id=%s",
        verification_id, payload.cliente, payload.artifact_id,
    )

    return VerifyAcceptedResponse(
        verification_id=verification_id,
        status=VerificationStatus.QUEUED,
        poll_url=f"/verificador/verify/{verification_id}",
    )


@router.get(
    "/verify/{verification_id}",
    response_model=VerificationResult,
    dependencies=[Depends(require_api_key)],
)
async def get_verification(verification_id: str) -> VerificationResult:
    result = storage.get(verification_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verificación {verification_id} no encontrada",
        )
    return result
