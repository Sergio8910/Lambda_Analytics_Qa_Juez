from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AgentRef(BaseModel):
    module: str
    function: str = "run_agent"

    model_config = {"extra": "forbid"}


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    ts: Optional[str] = None

    model_config = {"extra": "forbid"}


class EvaluateRequest(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None
    prompt_base: Optional[str] = None
    metrics: Optional[List[Dict[str, Any]]] = None
    config: Optional[Dict[str, Any]] = None
    cases: Optional[List[Dict[str, Any]]] = None
    mode: Literal["run_agent", "replay"] = "run_agent"
    agent_ref: Optional[AgentRef] = None
    conversation: Optional[List[ConversationTurn]] = None
    retrieval_context: Optional[List[Any]] = None
    n_cases: int = Field(default=30, ge=1, le=50)
    audit_mode: Literal["balanced", "enterprise"] = "balanced"
    return_pdf: bool = False
    seed: Optional[int] = None

    model_config = {"extra": "forbid"}


class EvaluateResponse(BaseModel):
    report: Dict[str, Any]
    pdf_base64: Optional[str] = None

    model_config = {"extra": "forbid"}


class GenerateCasesRequest(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict)
    prompt: Optional[str] = None
    prompt_base: Optional[str] = None
    retrieval_context: Optional[List[Any]] = None
    n_cases: int = Field(default=30, ge=1, le=50)
    seed: Optional[int] = None
    run_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class GenerateCasesResponse(BaseModel):
    cases: List[Dict[str, Any]]
    n_cases: int
    seed: Optional[int] = None

    model_config = {"extra": "forbid"}


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
        "evaluación. Muestra qué reglas y qué datos se usarían si lanzas /v1/evaluate."
    )

    model_config = {"extra": "forbid"}


class AutogenAgentHttp(BaseModel):
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = 10000

    model_config = {"extra": "forbid"}


class AutogenEvaluateRequest(BaseModel):
    agent_name: str
    prompt_base: str
    n_cases: int = Field(default=30, ge=1, le=50)
    metrics: List[str] = Field(default_factory=list)
    audit_mode: Literal["balanced", "enterprise"] = "balanced"
    seed: Optional[int] = None
    agent_http: AutogenAgentHttp
    rag_id: Optional[str] = None
    return_pdf: bool = False

    model_config = {"extra": "forbid"}


class AutogenEvaluateResponse(BaseModel):
    report: Dict[str, Any]
    pdf_base64: Optional[str] = None

    model_config = {"extra": "forbid"}


class UploadRagResponse(BaseModel):
    rag_id: str
    path: str

    model_config = {"extra": "forbid"}
