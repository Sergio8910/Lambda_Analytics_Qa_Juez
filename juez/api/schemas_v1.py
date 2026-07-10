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


class EvaluationPlanRequest(BaseModel):
    """Request para previsualizar QUÉ se le va a evaluar a un agente.

    Solo necesita el prompt del agente. Es 100% de solo-lectura: NO ejecuta al
    agente ni corre la evaluación. Devuelve el perfil detectado, las reglas
    (métricas + umbrales) que se aplicarían y los datos (casos sintéticos).
    """

    prompt_base: str = Field(..., min_length=1, description="System prompt del agente a evaluar")
    metrics: Optional[List[str]] = Field(
        None,
        description="Nombres de métricas a aplicar. Si se omite, se listan TODAS las disponibles del catálogo.",
    )
    n_cases: int = Field(default=10, ge=1, le=50, description="Cuántos casos sintéticos generar para la vista previa")
    seed: Optional[int] = Field(None, description="Semilla para reproducir los mismos casos")
    incluir_casos: bool = Field(True, description="Si False, devuelve solo perfil y reglas (sin generar datos)")

    model_config = {"extra": "forbid"}


class EvaluationPlanResponse(BaseModel):
    """Lo que se le va a evaluar a un agente: perfil + reglas + datos."""

    perfil_agente: Dict[str, Any] = Field(
        default_factory=dict,
        description="Lo que el Juez detectó del agente (idioma, dominio, formato esperado, rigor).",
    )
    reglas: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Métricas/criterios que se aplicarían, con tipo, umbral y requisitos.",
    )
    datos: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Casos de prueba sintéticos que se usarían para evaluar (vacío si incluir_casos=False).",
    )
    resumen: Dict[str, Any] = Field(
        default_factory=dict,
        description="Conteos: nº de reglas, nº de casos, distribución por tag.",
    )
    nota_metodo: str = (
        "Vista previa de solo-lectura: NO se ejecuta al agente ni se corre la "
        "evaluación. Muestra qué reglas y qué datos se usarían si lanzas /api/v1/evaluate."
    )

    model_config = {"extra": "forbid"}


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
    total_conversaciones: int = Field(20, ge=0, le=500)
    concurrencia: int = Field(3, ge=1, le=20)
    escenarios: List[str] = Field(default_factory=list)
    modo_ejecucion: Literal["sandbox", "real"] = Field(
        "sandbox",
        description="sandbox = pruebas sin side effects; real = dispara n8n/ElevenLabs reales.",
    )
    reference_dataset_id: Optional[str] = Field(
        None,
        description="ID de un dataset de referencia (POST /reference-data/ingest). Si trae payload_template, se usa para disparar el webhook con la forma real que el flujo espera.",
    )
    openai_key: Optional[str] = None
    n8n_api_key: Optional[str] = Field(None, description="Override de N8N_API_KEY")
    n8n_base_url: Optional[str] = Field(None, description="Override de N8N_BASE_URL (para descargas por ID)")
    evaluate_artifact: bool = Field(True, description="Si el agente tiene spec de artefacto, evalua su salida (ej. PDF). Por defecto activo y sintetico (no dispara el flujo real). No-op si el agente no tiene spec.")
    modo_qa: Literal["tecnico", "funcional", "ambos"] = Field("ambos", description="Tipo de QA: tecnico (estructura/codigo/seguridad), funcional (objetivos/salida/negocio) o ambos.")
    artifact_agent_id: Optional[str] = Field(None, description="Override del agent_id usado para buscar la spec de artefacto")


class EvalPipelineRequest(BaseModel):
    """Request para evaluar un pipeline completo (ElevenLabs + N flujos n8n)."""

    nombre: str = Field("Pipeline", description="Nombre del pipeline para el reporte")
    eleven_ids: List[str] = Field(default_factory=list, description="Lista de IDs de agentes ElevenLabs")
    n8n_flows: List[N8nFlowSource] = Field(default_factory=list, description="Lista de flujos n8n a incluir")
    total_conversaciones: int = Field(20, ge=0, le=500)
    concurrencia: int = Field(3, ge=1, le=20)
    escenarios: List[str] = Field(default_factory=list)
    modo_ejecucion: Literal["sandbox", "real"] = Field(
        "sandbox",
        description="sandbox = pruebas sin side effects; real = dispara n8n/ElevenLabs reales.",
    )
    reference_dataset_id: Optional[str] = Field(
        None,
        description="ID de un dataset de referencia (POST /reference-data/ingest). Si trae payload_template, se usa para disparar el webhook con la forma real que el flujo espera.",
    )
    openai_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    n8n_api_key: Optional[str] = None
    n8n_base_url: Optional[str] = None


class EvalProyectoRequest(BaseModel):
    """Request de la evaluación UNIFICADA de un proyecto (contrato firme).

    Combina La Colmena (análisis de construcción sobre `prompt` + `n8n_flows`) y
    las conversaciones (`run_pipeline` sobre `eleven_ids` + `n8n_flows`). Devuelve
    un job cuyo `result` sigue el contrato de `consolidar_proyecto`:
    `score`, `estado`, `problemas[]`, `mejoras[]` (con antes/después real del prompt).

    `prompt` es lo que habilita la mejora aplicable del prompt: pásalo con el
    system prompt actual del agente para recibir un `antes`/`despues` concreto.
    """

    nombre: str = Field("Proyecto", description="Nombre del proyecto para el reporte")
    prompt: str = Field(
        "",
        description="System prompt actual del agente. Habilita la mejora aplicable (antes/después).",
    )
    eleven_ids: List[str] = Field(default_factory=list, description="IDs de agentes ElevenLabs (voz)")
    n8n_flows: List[N8nFlowSource] = Field(default_factory=list, description="Flujos n8n del proyecto")
    total_conversaciones: int = Field(10, ge=0, le=500)
    concurrencia: int = Field(3, ge=1, le=20)
    escenarios: List[str] = Field(default_factory=list)
    incluir_conversaciones: bool = Field(
        True,
        description="Si False, solo corre La Colmena (rápido y barato, sin simular conversaciones).",
    )
    incluir_dinamicas: bool = Field(
        False,
        description="Obreras dinámicas de La Colmena (adversarial/edge/performance/propósito). Cuestan tokens.",
    )
    reglas_negocio: List[str] = Field(
        default_factory=list,
        description="Reglas de negocio explícitas (alta confianza) para verificación funcional y gates automáticos.",
    )
    objetivos: Optional[Dict[str, List[Dict[str, Any]]]] = Field(
        None,
        description="Objetivos declarados por flujo n8n (nombre_del_flujo -> lista de objetivos) para objectives.py.",
    )
    reference_dataset_id: Optional[str] = Field(
        None,
        description="ID de un dataset de referencia previamente ingerido (POST /reference-data/ingest) para usar datos reales en las conversaciones de prueba.",
    )
    openai_key: Optional[str] = None
    elevenlabs_key: Optional[str] = None
    n8n_api_key: Optional[str] = None
    n8n_base_url: Optional[str] = None
    modo_ejecucion: Literal["sandbox", "real"] = Field(
        "sandbox",
        description="sandbox = pruebas sin side effects; real = dispara n8n/ElevenLabs reales.",
    )


# =============================================================================
# RESPONSE MODELS
# =============================================================================


JobStatus = Literal["queued", "running", "completed", "failed"]
JobKind = Literal["elevenlabs", "n8n", "pipeline", "proyecto", "failure"]


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


# =============================================================================
# VERIFICACIÓN SINTÉTICA DE OBJETIVOS (sin disparar nada)
# =============================================================================


class ObjectiveInput(BaseModel):
    """Un objetivo declarado del flujo (qué define que cumplió su propósito).

    Espejo liviano de `juez.evaluation.n8n.objectives.Objective` para la API.
    """

    id: str = Field(..., description="Slug del objetivo, ej. 'crear_ticket'")
    descripcion: str = Field("", description="Descripción legible")
    kind: Literal[
        "send_email", "create_ticket", "http_request", "db_write", "db_read",
        "generate_file", "respond_webhook", "ai_response", "send_message", "custom",
    ] = Field("custom", description="Tipo conocido de objetivo")
    node_type_contains: List[str] = Field(default_factory=list)
    param_contains: List[str] = Field(default_factory=list)
    required_params: List[str] = Field(default_factory=list)
    requires_credentials: Optional[bool] = None
    min_count: int = Field(1, ge=1)
    severity_if_missing: Literal["critical", "high", "medium", "low", "info"] = "high"


class N8nFailurePayload(BaseModel):
    """Payload del Error Workflow de n8n (errorTrigger) que llega al Juez.

    Permisivo a propósito: el shape varía entre versiones de n8n. Solo se leen
    `execution` y `workflow` de forma tolerante.
    """

    execution: Optional[Dict[str, Any]] = Field(None, description="Datos de la ejecución fallida (id, url, error, lastNodeExecuted)")
    workflow: Optional[Dict[str, Any]] = Field(None, description="Datos del flujo que falló (id, name; opcionalmente nodes)")

    model_config = {"extra": "allow"}


class VerifyObjectivesRequest(BaseModel):
    """Request de verificación sintética de objetivos de un flujo n8n.

    NO dispara ni ejecuta nada: solo analiza el JSON del flujo. El flujo se pasa
    en `flow.json_content` (la forma directa, sin tocar n8n).
    """

    flow: N8nFlowSource = Field(..., description="Fuente del flujo. Usa flow.json_content para el modo sintético puro.")
    objectives: List[ObjectiveInput] = Field(..., min_length=1, description="Objetivos declarados a verificar")
