"""Prompt Eval — producto standalone para evaluar system prompts.

Recibe un prompt (texto), lo juzga contra ~26 reglas determinísticas + un
LLM-as-judge opcional, y devuelve un análisis exhaustivo con score global,
scores por dimensión, hallazgos detallados y recomendaciones priorizadas.

**No es el Juez.** El Juez evalúa al agente completo (flow + tools + persona).
**No es el Verificador.** El Verificador audita artefactos post-ejecución.
Prompt Eval audita el system prompt y nada más — entrada texto, salida score.

API: `POST /prompt_eval/evaluate` (síncrono).
Puerto default: 8002.
"""
from .evaluator import evaluate_prompt
from .models import (
    Dimension,
    Finding,
    PromptEvalRequest,
    PromptEvalResult,
    Severity,
)

__all__ = [
    "evaluate_prompt",
    "PromptEvalRequest",
    "PromptEvalResult",
    "Finding",
    "Severity",
    "Dimension",
]
