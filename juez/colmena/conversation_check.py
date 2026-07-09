"""Obrera dinamica: corre conversaciones REALES contra el webhook de un flujo n8n.

A diferencia de TODO el resto de Colmena (100% estatico o LLM-en-seco), esta
obrera SI dispara peticiones HTTP reales contra el webhook declarado -- si el
flujo escribe en una BD, manda un correo o cobra un pago, eso ocurre DE
VERDAD. Por eso es estrictamente opt-in y requiere DOS cosas explicitas:

  1. El proyecto declara la URL del webhook en un manifiesto explicito
     (webhooks_n8n.json en la raiz), nombre_del_flujo -> URL. Sin manifiesto,
     cero llamadas.
  2. El caller activa el modo conversaciones-reales explicitamente (separado
     de incluir_dinamicas, porque esto es categoricamente mas riesgoso que
     los chequeos LLM-en-seco existentes: tiene efectos reales en sistemas
     productivos).

Reusa integramente lo ya construido, sin duplicar logica:
  - N8nAnalyzer (juez/evaluar_n8n.py): analisis estatico del flujo (nodos_ia,
    herramientas) -- no requiere LLM.
  - _convertir_analisis_para_contra_agente: adapta ese analisis al formato
    que espera el generador de conversaciones.
  - generar_batch / ejecutar_batch (contra_agente): el mismo motor de
    conversaciones que ya corre para ElevenLabs, incluida la categoria
    "recorrido_completo" (cobertura de todas las tools en una conversacion).
  - N8nAdapter: adapter real que dispara HTTP contra el webhook.

IMPORTANTE: los hallazgos que produce esta obrera SIEMPRE quedan con
auto_fix_available=False. Nunca deben disparar el fixer automatico -- solo
son para revision humana.
"""
from __future__ import annotations

import os
from typing import Any

from .models import NormalizedFinding
from .workers import FindingBuilder

_SEVERIDAD_POR_CATEGORIA = {
    "seguridad": "critical",
    "herramienta": "high",
    "recorrido_completo": "high",
    "agresivo": "medium",
    "multi_turno": "medium",
}
_DEFAULT_TOTAL_CONVERSACIONES = 6


def _llm_disponible() -> bool:
    if not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
        return True
    except Exception:
        return False


def verificar_conversaciones_reales(
    nombre_flujo: str,
    workflow_json: dict[str, Any],
    webhook_url: str,
    *,
    total_conversaciones: int = _DEFAULT_TOTAL_CONVERSACIONES,
) -> list[NormalizedFinding]:
    """Corre `total_conversaciones` conversaciones reales contra `webhook_url`
    y devuelve un hallazgo por cada una que fallo, mas un resumen informativo.

    Nunca lanza -- cualquier fallo de infraestructura (import, red, timeout)
    se reporta como un hallazgo 'info', no como una excepcion.
    """
    builder = FindingBuilder()
    if not webhook_url:
        return []
    if not _llm_disponible():
        return [builder.make(
            severity="info", category="workflow", source="conversation_check",
            title=f"[{nombre_flujo}] conversaciones reales no ejecutadas: falta OPENAI_API_KEY",
            description="El juicio de cada turno de la conversacion requiere un modelo LLM disponible.",
            file=nombre_flujo,
        )]

    try:
        from juez.evaluar_n8n import N8nAnalyzer, _convertir_analisis_para_contra_agente
        from juez.evaluation.contra_agente.adapters.n8n import N8nAdapter
        from juez.evaluation.contra_agente.evaluator import TurnEvaluator
        from juez.evaluation.contra_agente.generator import generar_batch
        from juez.evaluation.contra_agente.pool import ejecutar_batch
    except Exception as exc:
        return [builder.make(
            severity="info", category="workflow", source="conversation_check",
            title=f"[{nombre_flujo}] modulo de conversaciones reales no disponible",
            description=f"{type(exc).__name__}: {exc}",
            file=nombre_flujo,
        )]

    openai_key = os.getenv("OPENAI_API_KEY", "")
    try:
        analisis_n8n = N8nAnalyzer(workflow_json).analizar()
        analisis_ca = _convertir_analisis_para_contra_agente(analisis_n8n)
        batch = generar_batch(
            analisis_ca, nombre_flujo, total=total_conversaciones,
            adapter="n8n", openai_key=openai_key,
        )

        def _adapter_factory(_tipo: str, _agent_id: str):
            return N8nAdapter(webhook_url=webhook_url)

        evaluator = TurnEvaluator(openai_key=openai_key)
        batch_result = ejecutar_batch(batch, _adapter_factory, evaluator, openai_key=openai_key)
    except Exception as exc:
        return [builder.make(
            severity="info", category="workflow", source="conversation_check",
            title=f"[{nombre_flujo}] error ejecutando conversaciones reales contra el webhook",
            description=f"{type(exc).__name__}: {exc}",
            file=nombre_flujo, evidence=webhook_url,
        )]

    findings: list[NormalizedFinding] = []
    for conv in batch_result.results:
        if conv.passed:
            continue
        severidad = _SEVERIDAD_POR_CATEGORIA.get(conv.category, "medium")
        findings.append(builder.make(
            severity=severidad, category="workflow", source="conversation_check",
            title=f"[{nombre_flujo}] conversacion real '{conv.plan_id}' ({conv.category}) fallo contra produccion",
            description=conv.diagnosis or "La conversacion no cumplio el criterio de exito esperado.",
            file=nombre_flujo, evidence=webhook_url,
            impact="El flujo, corriendo de verdad contra su webhook, no maneja bien este escenario real de conversacion.",
            recommendation="Revisar la transcripcion completa de la conversacion y corregir el flujo o el prompt del agente.",
        ))

    findings.append(builder.make(
        severity="info", category="workflow", source="conversation_check",
        title=f"[{nombre_flujo}] {batch_result.passed}/{batch_result.total} conversaciones reales pasaron ({batch_result.pass_rate:.0%})",
        description="Conversaciones reales ejecutadas contra el webhook de produccion declarado en webhooks_n8n.json.",
        file=nombre_flujo, evidence=webhook_url,
    ))
    return findings
