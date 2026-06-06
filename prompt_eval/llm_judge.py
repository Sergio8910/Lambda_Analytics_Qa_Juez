"""LLM-as-judge para el evaluador de prompts.

Las reglas determinísticas (rules.py) son rápidas y baratas pero no pueden
detectar:
  - Contradicciones semánticas (ej. 'sé conciso' + 'da explicaciones detalladas')
  - Ambigüedad real vs aparente
  - Coherencia rol↔objetivo↔tono
  - Lagunas no obvias (cosas que faltan que un experto vería)

Este módulo le pide a un LLM que analice el prompt en esos términos y
emita findings adicionales en el mismo formato que las reglas estáticas.

Tolerante a fallos: si no hay OPENAI_API_KEY o el LLM falla, retorna lista
vacía y un flag — el evaluador igual funciona con solo reglas determinísticas.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .models import Dimension, Finding, Severity

log = logging.getLogger("prompt_eval.llm_judge")


_SYSTEM = """Eres un auditor experto de system prompts para agentes LLM.

Te van a pasar un system prompt y opcionalmente contexto (rol esperado, tools, idioma esperado).
Tu tarea es identificar problemas que NO pueden detectarse con reglas regex/heurísticas:

1. Contradicciones internas (instrucciones que se anulan entre sí).
2. Ambigüedades reales que harían que el modelo improvise.
3. Coherencia rol ↔ objetivo ↔ tono ↔ formato.
4. Lagunas no obvias (cosas importantes que faltan dada la categoría del agente).
5. Frases mal calibradas (over-restrictive, under-specified, condicionales rotos).

Reglas estrictas de salida:
- Devuelve EXCLUSIVAMENTE JSON válido con esta forma:
{
  "findings": [
    {
      "titulo": "string corto",
      "descripcion": "explicación clara del problema",
      "recomendacion": "qué cambiar concretamente",
      "evidencia": "fragmento del prompt (cita literal o cuasi-literal)",
      "dimension": "claridad|estructura|especificidad|guardrails|manejo_errores|estilo",
      "severity": "critical|high|medium|low|info"
    }
  ]
}
- Si no encuentras nada relevante, devuelve {"findings": []}.
- Máximo 8 findings. Prioriza por impacto.
- No repitas los problemas obvios que regex ya detecta (rol ausente, formato no declarado, etc.).
  Vos enfocate en lo SEMÁNTICO."""


_USER_TEMPLATE = """Contexto del agente:
{ctx_block}

==== SYSTEM PROMPT A AUDITAR ====
{prompt}
==== FIN ===="""


def _ctx_block(ctx: Dict[str, Any]) -> str:
    pieces: List[str] = []
    if ctx.get("nombre"):
        pieces.append(f"- Nombre: {ctx['nombre']}")
    if ctx.get("domain"):
        pieces.append(f"- Dominio: {ctx['domain']}")
    if ctx.get("expected_language"):
        pieces.append(f"- Idioma esperado: {ctx['expected_language']}")
    if ctx.get("expected_output_format"):
        pieces.append(f"- Formato esperado: {ctx['expected_output_format']}")
    if ctx.get("tools"):
        pieces.append(f"- Tools conectadas: {', '.join(ctx['tools'])}")
    return "\n".join(pieces) if pieces else "(sin contexto adicional)"


def _coerce_finding(d: Dict[str, Any]) -> Optional[Finding]:
    """Convierte un dict del LLM en un Finding tolerando campos parciales."""
    try:
        dim_raw = (d.get("dimension") or "claridad").lower().strip()
        try:
            dim = Dimension(dim_raw)
        except ValueError:
            dim = Dimension.CLARIDAD
        sev_raw = (d.get("severity") or "medium").lower().strip()
        try:
            sev = Severity(sev_raw)
        except ValueError:
            sev = Severity.MEDIUM
        titulo = (d.get("titulo") or "").strip()
        descripcion = (d.get("descripcion") or "").strip()
        if not titulo or not descripcion:
            return None
        return Finding(
            rule_id="LLM",
            rule_name="llm_judge",
            dimension=dim,
            severity=sev,
            titulo=titulo[:200],
            descripcion=descripcion[:2000],
            recomendacion=(d.get("recomendacion") or None),
            evidencia=(d.get("evidencia") or None),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("LLM finding inválido descartado: %s", exc)
        return None


def run_llm_judge(
    prompt: str,
    ctx: Dict[str, Any],
    model: Optional[str] = None,
    timeout_s: float = 30.0,
) -> Tuple[List[Finding], Dict[str, Any]]:
    """Devuelve (findings, meta). meta incluye modelo, tokens, error si falló.

    Si no hay OPENAI_API_KEY o el cliente OpenAI no está instalado, retorna
    ([], {"skipped": True, "reason": "..."}) sin lanzar excepción.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return [], {"skipped": True, "reason": "OPENAI_API_KEY no configurada"}

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return [], {"skipped": True, "reason": "paquete openai no instalado"}

    model = model or os.getenv("JUDGE_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key, timeout=timeout_s)

    user_msg = _USER_TEMPLATE.format(ctx_block=_ctx_block(ctx), prompt=prompt[:12000])

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM judge fallo: %s: %s", type(exc).__name__, exc)
        return [], {"skipped": True, "reason": f"{type(exc).__name__}: {exc}"}

    content = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.warning("LLM judge devolvió JSON inválido: %s", exc)
        return [], {"skipped": True, "reason": "JSON inválido del LLM"}

    raw_findings = parsed.get("findings", []) or []
    findings: List[Finding] = []
    for item in raw_findings[:8]:
        f = _coerce_finding(item)
        if f is not None:
            findings.append(f)

    usage = getattr(resp, "usage", None)
    meta: Dict[str, Any] = {
        "model": model,
        "skipped": False,
    }
    if usage is not None:
        try:
            meta["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
            meta["completion_tokens"] = getattr(usage, "completion_tokens", None)
            meta["total_tokens"] = getattr(usage, "total_tokens", None)
        except Exception:
            pass
    return findings, meta
