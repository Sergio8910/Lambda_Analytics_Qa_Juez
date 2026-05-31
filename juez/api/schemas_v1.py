"""Schemas Pydantic para la API v1 del Juez (evaluador nuevo).

Convive con `api/schemas.py` (juez legacy basado en DeepEval).
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# =============================================================================
# REQUEST MODELS
# =============================================================================


class N8nFlowSource(BaseModel):
    """Una fuente para identificar un flujo n8n.

    Se acepta UNA de estas tres formas (en orden de prioridad):
      - `json_content`: el JSON completo del flujo
      - `url`: URL del flujo en n8n (https://n8n.../workflow/ID) o solo el ID
      - `workflow_id`: ID puro del flujo (requiere `n8n_base_url` global)
    """

    url: Optional[str] = Field(None, description="URL completa del flujo o solo el ID")
    workflow_id: Optional[str] = Field(None, description="ID puro del flujo n8n")
    json_content: Optional[Dict[str, Any]] = Field(None, description="JSON completo del flujo")
    webhook_url: Optional[str] = Field(
        None,
        description="URL del webhook para pruebas dinámicas. Si no se pasa, se extrae del JSON.",
    )


class EvalElevenLabsRequest(BaseModel):
    """Request para evaluar un agente de ElevenLabs.

    El campo `target_id` acepta tanto:
      - Un agent_id directo (prefijo `agent_*`) — evalúa la versión "live" del agente.
      - Un branch_id (prefijo `agtbrch_*`) — resuelve al agente padre, descarga
        su configuración en esa rama específica y la evalúa. Si
        `include_n8n_flows=True` (default), además descubre automáticamente los
        flujos n8n que el agente llama vía webhook tools y los evalúa también,
        retornando todo como un pipeline unificado.

    `agent_id` se mantiene como alias deprecado por compatibilidad.
    """

    target_id: Optional[str] = Field(
        None,
        description="ID del recurso a evaluar: agent_* o agtbrch_*",
    )
    agent_id: Optional[str] = Field(
        None,
        description="(deprecado, usa target_id) — solo agent_*",
    )
    include_n8n_flows: bool = Field(
        True,
        description=(
            "Si target_id es un branch, también detectar y evaluar los flujos n8n "
            "que el agente llama vía sus webhook tools. Si es False, evalúa "
            "solo el agente bajo el branch."
        ),
    )
    total_conversaciones: int = Field(20, ge=0, le=100, description="Número de conversaciones de prueba (0 = solo estático)")
    concurrencia: Optional[int] = Field(None, description="Concurrencia del contra-agente (auto si no se pasa)")
    escenarios: List[str] = Field(default_factory=list, description="Escenarios específicos adicionales en lenguaje natural")
    openai_key: Optional[str] = Field(None, description="Override de OPENAI_API_KEY (opcional)")
    elevenlabs_key: Optional[str] = Field(None, description="Override de ELEVENLABS_API_KEY (opcional)")
    n8n_api_key: Optional[str] = Field(None, description="Override de N8N_API_KEY (necesario si include_n8n_flows=True)")
    n8n_base_url: Optional[str] = Field(None, description="Override de N8N_BASE_URL (instancia n8n a consultar)")

    def resolved_target(self) -> str:
        """Retorna el ID efectivo a evaluar (target_id tiene prioridad sobre agent_id)."""
        return (self.target_id or self.agent_id or "").strip()


class EvalN8nRequest(BaseModel):
    """Request para evaluar un único flujo n8n."""

    flow: N8nFlowSource = Field(..., description="Origen del flujo a evaluar")
    total_conversaciones: int = Field(20, ge=0, le=100)
    concurrencia: int = Field(3, ge=1, le=20)
    escenarios: List[str] = Field(default_factory=list)
    openai_key: Optional[str] = None
    n8n_api_key: Optional[str] = Field(None, description="Override de N8N_API_KEY")
    n8n_base_url: Optional[str] = Field(None, description="Override de N8N_BASE_URL (para descargas por ID)")
    evaluate_artifact: bool = Field(False, description="Si el agente tiene spec de artefacto, dispara y evalua su salida (ej. PDF)")
    artifact_agent_id: Optional[str] = Field(None, description="Override del agent_id usado para buscar la spec de artefacto")


class EvalPipelineRequest(BaseModel):
    """Request para evaluar un pipeline completo (ElevenLabs + N flujos n8n)."""

    nombre: str = Field("Pipeline", description="Nombre del pipeline para el reporte")
    eleven_ids: List[str] = Field(default_factory=list, description="Lista de IDs de agentes ElevenLabs")
    n8n_flows: List[N8nFlowSource] = Field(default_factory=list, description="Lista de flujos n8n a incluir")
    total_conversaciones: int = Field(20, ge=0, le=100)
    concurrencia: int = Field(3, ge=1, le=20)
    escenarios: List[str] = Field(default_factory=list)
    openai_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    n8n_api_key: Optional[str] = None
    n8n_base_url: Optional[str] = None


# =============================================================================
# RESPONSE MODELS
# =============================================================================


JobStatus = Literal["queued", "running", "completed", "failed"]
JobKind = Literal["elevenlabs", "n8n", "pipeline"]


class JobCreatedResponse(BaseModel):
    """Respuesta inmediata al crear un job."""

    job_id: str
    kind: JobKind
    status: JobStatus
    created_at: str
    poll_url: str = Field(..., description="Endpoint para consultar el estado")


class JobProgress(BaseModel):
    step: str = Field(..., description="Descripción del paso actual")
    percent: int = Field(0, ge=0, le=100)


class JobStatusResponse(BaseModel):
    """Respuesta de polling del estado de un job."""

    job_id: str
    kind: JobKind
    status: JobStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: Optional[JobProgress] = None
    result: Optional[Dict[str, Any]] = Field(
        None,
        description="Resultado completo cuando status=completed. Contiene scores, problemas, reporte_txt, etc.",
    )
    error: Optional[str] = Field(None, description="Mensaje de error cuando status=failed")


class JobListResponse(BaseModel):
    """Listado de jobs recientes."""

    jobs: List[JobStatusResponse]
    total: int


class HealthResponse(BaseModel):
    status: str
    version: str
    evaluator_available: bool
    contra_agente_available: bool
