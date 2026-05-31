"""Modelos Pydantic v2 del Verificador.

Estos modelos son el contrato HTTP y la representación canónica que circula
entre componentes. NUNCA serializar campos sensibles (credenciales) en estos
modelos.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class VerificationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Verdict(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    UNVERIFIABLE = "UNVERIFIABLE"


class Severidad(str, Enum):
    INFO = "INFO"
    BAJO = "BAJO"
    MEDIO = "MEDIO"
    ALTO = "ALTO"
    CRITICO = "CRITICO"


# ─────────────────────────────────────────────────────────────────────────────
# Source (de dónde se baja el artefacto)
# ─────────────────────────────────────────────────────────────────────────────

class DriveSource(BaseModel):
    """Archivo en Google Drive."""
    type: Literal["drive"] = "drive"
    file_id: str = Field(..., min_length=1, max_length=256)


class InlineSource(BaseModel):
    """Bytes del artefacto incluidos en el request (base64).

    Útil para flujos sintéticos donde el productor del artefacto (ej. el
    Juez en modo e2e sintético) ya tiene el blob en memoria y no necesita
    intermediar por Drive/S3. NO se persiste el blob en el verificador.
    """
    type: Literal["inline"] = "inline"
    blob_base64: str = Field(
        ..., min_length=1,
        description="Bytes del artefacto codificados en base64 (sin padding extra).",
    )


# Discriminated union de fuentes posibles. Pydantic resuelve por el campo `type`.
Source = Annotated[Union[DriveSource, InlineSource], Field(discriminator="type")]


# ─────────────────────────────────────────────────────────────────────────────
# Request del webhook
# ─────────────────────────────────────────────────────────────────────────────

class VerifyWebhookRequest(BaseModel):
    """Payload que n8n manda al verificador al final del flow."""
    cliente: str = Field(..., min_length=1, max_length=64,
                         description="Cliente registrado (ej. 'abad').")
    artifact_type: str = Field(..., min_length=1, max_length=32,
                               description="Tipo de artefacto: 'pdf', futuro 'image', etc.")
    artifact_id: str = Field(..., min_length=1, max_length=128,
                             description="ID que el cliente sabe interpretar (ej. inventario_id). "
                                         "Es la clave de idempotencia.")
    source: Source
    metadata: Dict[str, Any] = Field(default_factory=dict,
                                     description="Trazabilidad libre (contrato_id, n8n_execution_id...).")

    model_config = {"extra": "forbid"}


# ─────────────────────────────────────────────────────────────────────────────
# Resultados internos
# ─────────────────────────────────────────────────────────────────────────────

class Issue(BaseModel):
    """Un problema detectado por un check."""
    severidad: Severidad
    mensaje: str
    check: str = Field(..., description="Nombre del check que lo produjo.")
    detalles: Dict[str, Any] = Field(default_factory=dict)


class CheckResult(BaseModel):
    """Resultado de un check individual (integridad, conteo_fotos, etc.)."""
    name: str
    verdict: Verdict
    score: float = Field(ge=0.0, le=1.0)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    issues: List[Issue] = Field(default_factory=list)


class InspectorReport(BaseModel):
    """Lo que retorna un Inspector después de evaluar un artefacto."""
    checks: List[CheckResult]
    overall_verdict: Verdict
    overall_score: float = Field(ge=0.0, le=1.0)


class ExpectedSnapshot(BaseModel):
    """Lo que la BD del cliente dice que el artefacto debería contener.

    IMPORTANTE: este modelo puede llevar PII si se llena crudo. En storage
    se debe ofuscar antes de persistir (ver plan §PII).
    """
    artifact_id: str
    # Conteos esperados (universal para cualquier multimedia)
    counts: Dict[str, int] = Field(default_factory=dict,
                                   description="Ej. {'fotos': 47, 'ambientes': 6}.")
    # Estructura esperada (cliente-específica, opaca al verifier)
    structure: Dict[str, Any] = Field(default_factory=dict,
                                      description="Cliente-específico. Ej. {'fotos_por_ambiente': {...}}.")
    # Strings que deben aparecer en el artefacto (contrato_id, propietario, etc.)
    required_strings: List[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Responses HTTP
# ─────────────────────────────────────────────────────────────────────────────

class VerifyAcceptedResponse(BaseModel):
    """Respuesta 202 al POST /verify."""
    verification_id: str
    status: VerificationStatus
    poll_url: str


class VerificationResult(BaseModel):
    """Respuesta del GET /verify/{id} (sea sin terminar o ya completo).

    Este es el shape que se persiste también en storage.
    """
    verification_id: str
    cliente: str
    artifact_type: str
    artifact_id: str
    status: VerificationStatus
    verdict: Optional[Verdict] = None
    score: Optional[float] = None
    checks: List[CheckResult] = Field(default_factory=list)
    issues: List[Issue] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    artifact_size_bytes: Optional[int] = None
    elapsed_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class HealthResponse(BaseModel):
    """Respuesta del GET /health."""
    status: Literal["ok"] = "ok"
    component: Literal["verificador"] = "verificador"
    version: str = "0.1.0"
    config: Dict[str, bool] = Field(default_factory=dict,
                                    description="Flags de capability (storage, abad, drive, auth).")
