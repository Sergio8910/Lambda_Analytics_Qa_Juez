"""Modelos Pydantic del evaluador de prompts.

Filosofía:
  - El request lleva el prompt + contexto mínimo opcional (rol esperado,
    idioma esperado, formato esperado, lista de tools si las hay).
  - La respuesta lleva un score global (0-100), scores por dimensión, lista
    de hallazgos con severidad, métricas, y recomendaciones priorizadas.
  - Nada de "verdadero/falso" — todo viene con severidad y peso para que el
    consumidor pueda tomar decisiones.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# =============================================================================
# REQUEST
# =============================================================================


class PromptEvalRequest(BaseModel):
    """Request del endpoint /api/v1/evaluate/prompt.

    `prompt` es el único campo obligatorio. Todos los demás son hints que
    ayudan a aplicar reglas más precisas (ej. si `expected_language='es'`,
    flageamos mezcla de idiomas con más severidad).
    """

    prompt: str = Field(..., min_length=1, description="System prompt a evaluar")
    nombre: Optional[str] = Field(
        None,
        description="Nombre del agente / prompt para los reportes (opcional)",
    )
    expected_language: Optional[Literal["es", "en", "pt", "fr"]] = Field(
        None,
        description="Idioma esperado. Si se pasa, se flagea mezcla con más rigor.",
    )
    expected_output_format: Optional[Literal["json", "markdown", "plain", "yaml", "html"]] = Field(
        None,
        description="Formato de salida esperado. Permite chequear si el prompt lo especifica.",
    )
    tools: List[str] = Field(
        default_factory=list,
        description="Nombres de tools conectadas al agente (si aplica). Permite chequear alineación tool↔prompt.",
    )
    domain: Optional[str] = Field(
        None,
        description="Dominio/industria del agente (free text, ej. 'real estate', 'banking'). Solo informativo.",
    )
    incluir_llm_judge: bool = Field(
        True,
        description=(
            "Si True (default) y hay OPENAI_API_KEY, se corren reglas cualitativas con LLM "
            "(claridad semántica, contradicciones, etc.). Si False, solo reglas determinísticas."
        ),
    )
    llm_model: Optional[str] = Field(
        None,
        description="Override del modelo del LLM judge (default: settings.JUDGE_MODEL).",
    )


# =============================================================================
# RESPONSE — primitivos
# =============================================================================


class Severity(str, Enum):
    """Severidad de un hallazgo.

    - critical: bloquea uso del prompt en producción.
    - high: error notorio que degrada calidad de respuestas.
    - medium: problema corregible que ayuda significativamente.
    - low: mejora cosmética / consistencia.
    - info: observación neutra, no es un error.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


# Cada regla deduce este peso del severity. Hace explícito el "qué pesa más"
# para que el consumidor pueda priorizar fixes.
SEVERITY_PENALTY = {
    Severity.CRITICAL: 70,
    Severity.HIGH: 28,
    Severity.MEDIUM: 13,
    Severity.LOW: 5,
    Severity.INFO: 0,
}


class Dimension(str, Enum):
    """Dimensiones exclusivas del análisis. Cada regla pertenece a UNA sola."""

    ESTRUCTURA = "estructura"
    CLARIDAD = "claridad"
    ESPECIFICIDAD = "especificidad"
    GUARDRAILS = "guardrails"
    MANEJO_ERRORES = "manejo_errores"
    ESTILO = "estilo"


# Pesos de cada dimensión en el score global. Suma = 1.0
DIMENSION_WEIGHTS: Dict[Dimension, float] = {
    Dimension.ESTRUCTURA: 0.25,
    Dimension.CLARIDAD: 0.20,
    Dimension.ESPECIFICIDAD: 0.20,
    Dimension.GUARDRAILS: 0.15,
    Dimension.MANEJO_ERRORES: 0.10,
    Dimension.ESTILO: 0.10,
}


class Finding(BaseModel):
    """Un hallazgo del análisis. Es la unidad de feedback al usuario."""

    rule_id: str = Field(..., description="Identificador estable de la regla, ej. R007")
    rule_name: str = Field(..., description="Nombre humano corto de la regla")
    dimension: Dimension = Field(..., description="Dimensión exclusiva")
    severity: Severity
    titulo: str = Field(..., description="Resumen del problema en una línea")
    descripcion: str = Field(..., description="Explicación detallada del problema")
    recomendacion: Optional[str] = Field(
        None, description="Qué cambiar concretamente para corregirlo"
    )
    evidencia: Optional[str] = Field(
        None,
        description="Fragmento del prompt que motivó el hallazgo (limitado a ~300 chars).",
    )
    posicion_aprox: Optional[int] = Field(
        None,
        description="Índice de caracter aproximado donde aparece el problema (si aplica).",
    )

    @property
    def penalty(self) -> int:
        """Penalty numérico que descuenta del score de la dimensión."""
        return SEVERITY_PENALTY[self.severity]


class DimensionScore(BaseModel):
    """Score por dimensión: 0-100, donde 100 es perfecto."""

    dimension: Dimension
    score: float = Field(..., ge=0, le=100)
    weight: float = Field(..., ge=0, le=1)
    findings_count: int = 0
    findings_by_severity: Dict[str, int] = Field(default_factory=dict)


class PromptMetrics(BaseModel):
    """Métricas crudas del prompt (descriptivo, no juzga)."""

    longitud_chars: int
    longitud_palabras: int
    longitud_lineas: int
    longitud_estimada_tokens: int = Field(
        ..., description="Estimación heurística (chars/4). Útil para FinOps."
    )
    idioma_detectado: str
    secciones_detectadas: List[str] = Field(
        default_factory=list,
        description="Headers tipo '### Reglas', '## Tono' encontrados en el prompt.",
    )
    placeholders_detectados: List[str] = Field(
        default_factory=list,
        description="Variables tipo {{var}} o {var} usadas en el prompt.",
    )
    menciona_tools: List[str] = Field(
        default_factory=list,
        description="Tools del request que aparecen mencionadas en el prompt (cualquier variante).",
    )


# =============================================================================
# RESPONSE — root
# =============================================================================


class PromptEvalResult(BaseModel):
    """Respuesta completa del endpoint."""

    nombre: Optional[str] = None
    score_global: float = Field(..., ge=0, le=100, description="Score 0-100 — la métrica concreta")
    veredicto: Literal["excelente", "bueno", "aceptable", "deficiente", "critico"]
    dimensiones: List[DimensionScore]
    findings: List[Finding]
    findings_resumen: Dict[str, int] = Field(
        default_factory=dict,
        description="Conteo de findings por severidad: {critical: N, high: N, ...}",
    )
    metricas: PromptMetrics
    top_recomendaciones: List[str] = Field(
        default_factory=list,
        description="Top 5 acciones priorizadas por impacto (severity * peso de dimensión).",
    )
    juez_version: int = 2
    llm_judge_aplicado: bool = False
    duracion_ms: Optional[int] = None
    meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extras (modelo LLM usado, hash del prompt, etc.).",
    )
