"""Router HTTP de Prompt Eval.

Endpoints:
  - POST /prompt_eval/evaluate     → evaluación síncrona
  - GET  /prompt_eval/rules        → catálogo de reglas (transparencia)
  - GET  /health                   → ping

El endpoint de evaluación es síncrono: el análisis es rápido (~50-500ms
sin LLM, ~2-8s con LLM). No vale la pena un job store async para esto.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Header, HTTPException

from .conversation import ConversationInput
from .evaluator import evaluate_prompt
from .models import (
    DIMENSION_WEIGHTS,
    SEVERITY_PENALTY,
    Dimension,
    PromptEvalRequest,
    PromptEvalResult,
    Severity,
)
from .rules import ALL_RULES
from .settings import settings

log = logging.getLogger("prompt_eval.router")

router = APIRouter()


def _require_api_key(x_api_key: str | None) -> None:
    expected = settings.API_KEY
    if not expected:
        return  # endpoint público si no se configuró API key
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/health")
def health() -> Dict[str, Any]:
    """Ping del servicio."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.ENV,
        "llm_judge_enabled": bool(settings.OPENAI_API_KEY),
    }


@router.post("/prompt_eval/evaluate", response_model=PromptEvalResult)
def evaluate(
    req: PromptEvalRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> PromptEvalResult:
    """Evalúa un system prompt y devuelve el análisis exhaustivo.

    Devuelve:
      - `score_global` 0-100 (la métrica concreta).
      - `veredicto` ('excelente' / 'bueno' / 'aceptable' / 'deficiente' / 'critico').
      - `dimensiones`: 6 dimensiones exclusivas (estructura, claridad,
        especificidad, guardrails, manejo_errores, estilo) con score, peso
        y conteo de findings.
      - `findings`: lista de hallazgos con regla, severidad, evidencia y
        recomendación.
      - `metricas`: longitud, idioma, secciones, placeholders, tools mencionadas.
      - `top_recomendaciones`: top 5 acciones priorizadas por impacto.

    Si `incluir_llm_judge=True` (default) y hay `OPENAI_API_KEY`, además de
    las reglas determinísticas se corre un LLM-as-judge para contradicciones,
    ambigüedades semánticas y lagunas. Si el LLM falla, el resultado se
    entrega igual con `llm_judge_aplicado=false`.
    """
    _require_api_key(x_api_key)
    return evaluate_prompt(req)


@router.post("/prompt_eval/evaluate-conversation")
def evaluate_conversation_endpoint(
    conv: ConversationInput,
    incluir_llm: bool = True,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Evalúa una CONVERSACIÓN (transcript ya ocurrido) del agente.

    Recibe el JSON de la conversación (agent_role, turns, metadata...) y evalúa
    de forma INDEPENDIENTE el desempeño del agente: chequeos determinísticos
    sobre los turnos + LLM-as-judge (si hay OPENAI_API_KEY) sobre su rol y
    conductas. El `prompt_adherence` auto-reportado NO se toma como verdad.

    Devuelve `score_global`, `veredicto`, dimensiones del LLM, criterios
    determinísticos, hallazgos, métricas y un `reporte_txt`.
    """
    _require_api_key(x_api_key)
    from .conversation import evaluate_conversation, render_conversation_report

    res = evaluate_conversation(conv, incluir_llm=incluir_llm)
    out = res.model_dump(mode="json")
    out["reporte_txt"] = render_conversation_report(res)
    return out


@router.get("/prompt_eval/rules")
def list_rules() -> Dict[str, Any]:
    """Catálogo de reglas determinísticas, sin ejecutar nada.

    Útil para que el consumidor entienda qué chequeamos y cómo se pesa cada
    dimensión.
    """
    reglas: List[Dict[str, Any]] = []
    for rule in ALL_RULES:
        name = getattr(rule, "__name__", "unknown")
        # Convención: rule_rNNN_<slug>
        rule_id = ""
        slug = name
        if name.startswith("rule_"):
            partes = name[5:].split("_", 1)
            rule_id = partes[0].upper()
            slug = partes[1] if len(partes) > 1 else ""
        reglas.append(
            {
                "rule_id": rule_id,
                "name": slug,
                "function": name,
                "docstring": (rule.__doc__ or "").strip().split("\n")[0],
            }
        )
    return {
        "total_reglas": len(reglas),
        "reglas": reglas,
        "dimensiones": {
            d.value: {"peso": w} for d, w in DIMENSION_WEIGHTS.items()
        },
        "penalidades": {sev.value: pen for sev, pen in SEVERITY_PENALTY.items()},
        "veredictos": {
            "excelente": ">=90",
            "bueno": ">=75",
            "aceptable": ">=60",
            "deficiente": ">=40",
            "critico": "<40",
        },
    }
