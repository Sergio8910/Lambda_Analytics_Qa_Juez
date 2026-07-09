"""Modelos Pydantic del módulo contra-agente."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AdaptiveLogic(BaseModel):
    """Lógica condicional para decidir el siguiente mensaje según la respuesta del agente."""
    conditions: List[Dict[str, str]] = Field(default_factory=list)
    # Ejemplo:
    # [
    #   {"if": "agent_invoked_tool", "then": "skip_to_closing"},
    #   {"if": "agent_asked_for_data", "then": "provide_partial_data"},
    #   {"else": "escalate_pressure"}
    # ]

    model_config = {"extra": "forbid"}


class TurnSpec(BaseModel):
    """Especificación de un turno dentro de una conversación."""
    turn_id: int
    turn_type: Literal["opener", "probe", "stress", "escalation", "recovery", "closing"]
    intent: str
    message_template: str
    fragmentos: Optional[List[str]] = None      # mensajes adicionales enviados en secuencia tras message_template
    fragmento_delay_ms: int = 800               # pausa entre fragmentos (ms)
    success_criteria: str
    metrics: List[str]
    adaptive_logic: Optional[AdaptiveLogic] = None
    variables: Dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class Persona(BaseModel):
    """Perfil del usuario simulado por el contra-agente."""
    name: str
    mood: Literal["cordial", "frustrado", "agresivo", "confuso", "curioso", "impaciente"]
    backstory: str
    language_style: Literal["formal", "informal", "coloquial"] = "informal"

    model_config = {"extra": "forbid"}


class ArtifactExpectation(BaseModel):
    """Caso e2e sintético: este plan debe terminar con auditoría de artefacto.

    Si está poblado, el `pool` después de ejecutar la conversación construye
    un PDF sintético, lo manda al Verificador (cliente `abad_synthetic` +
    source `inline`), y el verdict se mezcla con el score conversacional
    según `weight`.
    """
    artifact_type: Literal["pdf"] = "pdf"
    cliente: str = "abad_synthetic"       # cliente del Verificador (sintético por default)
    artifact_id: str                      # ej. "JUEZ-E2E-{batch_id}-{idx:02d}"
    weight: float = Field(default=0.30, ge=0.0, le=1.0)
    # Snapshot esperado y datos canónicos pasan a través del runtime — se
    # guardan acá para que la post-fase del pool pueda reconstruir todo.
    expected_snapshot: Dict[str, Any] = Field(default_factory=dict)
    canonical_data: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ConversationPlan(BaseModel):
    """Plan completo de una conversación. El contra-agente lo ejecuta turno a turno."""
    plan_id: str
    category: Literal[
        "happy_path", "herramienta", "recorrido_completo", "limite", "caos",
        "agresivo", "seguridad", "contexto_multiple", "multi_turno"
    ]
    severity: Literal["alta", "media", "baja"] = "media"
    tags: List[str] = Field(default_factory=list)
    success_threshold: float = 0.70
    max_turns: int = Field(ge=1, le=10)
    persona: Persona
    turns: List[TurnSpec]
    notes: Optional[str] = None
    # Modo e2e: si está poblado, el caso termina con auditoría del Verificador.
    artifact_expectation: Optional[ArtifactExpectation] = None

    model_config = {"extra": "forbid"}


class ConversationBatch(BaseModel):
    """Batch completo de N conversaciones a ejecutar."""
    batch_id: str
    agent_id: str
    adapter: Literal["elevenlabs", "n8n"]
    total: int
    concurrency: int = 10
    plans: List[ConversationPlan]

    model_config = {"extra": "forbid"}


class TurnResult(BaseModel):
    """Resultado de un turno ejecutado."""
    turn_id: int
    turn_type: str
    message_sent: str
    agent_response: str
    latency_ms: float
    scores: Dict[str, float]
    passed: bool
    reason: str
    adaptive_branch_taken: Optional[str] = None
    message_fragments: Optional[List[str]] = None  # fragmentos si fue turno fragmentado
    transport_debug: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}


class ConversationResult(BaseModel):
    """Resultado completo de una conversación."""
    plan_id: str
    category: str
    tags: List[str]
    passed: bool
    turn_results: List[TurnResult]
    collapse_turn: Optional[int] = None
    overall_score: float
    transcript: List[Dict[str, str]]
    latency_total_ms: float
    diagnosis: str
    # Si el plan era e2e (artifact_expectation poblado), la post-fase del pool
    # llena este campo con el verdict del Verificador. None = no aplica o
    # verificación skipped (Verificador caído / timeout).
    artifact_verdict: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}


class BatchResult(BaseModel):
    """Resultado consolidado de todas las conversaciones del batch."""
    batch_id: str
    agent_id: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    by_category: Dict[str, Dict[str, Any]]
    collapse_pattern: Dict[str, int]
    results: List[ConversationResult]
    recommendations: List[str]
    scorecard: Dict[str, float]
    # Resumen de consumo de tokens (modo e2e con MockAgent).
    # Estructura: ver CostMeter.summary() en synthetic/cost_meter.py.
    # None = no hubo tracking (modo no-e2e o cost_meter no inyectado).
    cost_summary: Optional[Dict[str, Any]] = None

    model_config = {"extra": "forbid"}
