"""Obrera dinamica: verifica si un prompt CUMPLE SU PROPOSITO declarado.

A diferencia de AgentPromptWorker (jailbreak/secretos por patron) y de
Exploradora/Ninera (juicio LLM ad-hoc de seguridad/edge cases), esta obrera
reusa la MISMA rubrica EXITO/FALLO ya probada del motor central del Juez
(juez.evaluation.core.task_success_rubric) para juzgar si el prompt, tal como
esta escrito, lograria cumplir la intencion declarada por el equipo -- no solo
si es seguro, sino si SIRVE para lo que dice que sirve.

Opt-in (corre junto a Exploradora/Ninera bajo incluir_dinamicas=True). Sin
OPENAI_API_KEY o sin proposito declarado, degrada con gracia. Costo trackeado
via CostMeter cuando se provee uno.
"""
from __future__ import annotations

import json
import os
from typing import Any

from juez.evaluation.core.task_success_rubric import build_task_success_rubric

_SUCCESS_THRESHOLD = 0.6


def _llm_disponible() -> bool:
    if not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
        return True
    except Exception:
        return False


def _juzgar_proposito(prompt_sistema: str, proposito: str) -> dict[str, Any]:
    """Una llamada LLM: simula una interaccion realista con el agente y la
    juzga con la MISMA rubrica task_success del motor central (no una
    inventada ad-hoc)."""
    from openai import OpenAI

    criteria, steps, _mode = build_task_success_rubric(proposito, tags=set())
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    sistema = (
        "Eres un evaluador de agentes de IA. Te doy el SYSTEM PROMPT de un agente "
        "y el PROPOSITO/OBJETIVO que se espera que cumpla. "
        "1) Inventa UNA entrada realista y especifica de un usuario real que pondria "
        "a prueba ese proposito. "
        "2) Simula, en base al system prompt dado, como respondería el agente a esa entrada. "
        "3) Juzga la respuesta simulada con este criterio: " + criteria + " "
        "Pasos a seguir: " + " ".join(steps) + " "
        'Responde SOLO JSON: {"entrada_simulada": "...", "respuesta_simulada": "...", '
        '"score": 0.0-1.0, "razon": "..."}'
    )
    user = f"PROPOSITO ESPERADO:\n{proposito[:1500]}\n\nSYSTEM PROMPT DEL AGENTE:\n{prompt_sistema[:4000]}"
    model = os.getenv("JUDGE_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": user}],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    usage = getattr(resp, "usage", None)
    data["_model"] = model
    data["_prompt_tokens"] = getattr(usage, "prompt_tokens", None) if usage is not None else None
    data["_completion_tokens"] = getattr(usage, "completion_tokens", None) if usage is not None else None
    return data


def verificar_proposito(c, purposes: dict[str, str], cost_meter=None) -> list[dict[str, Any]]:
    """Obrera dinamica: hallazgo si el prompt no lograria cumplir su proposito declarado."""
    from .colmena import _h

    prompt = getattr(c, "prompt", None)
    if not prompt:
        return []
    proposito = purposes.get(c.nombre)
    if not proposito:
        # Antes: return [] silencioso. Ahora deja rastro visible (info, no
        # penaliza score) para que el reporte nunca omita en silencio que el
        # cumplimiento de proposito de este componente no se verifico.
        return [_h("Proposito", "info",
                   f"[{c.nombre}] cumplimiento de proposito NO verificado: sin proposito declarado. "
                   f"Declara 'proposito_por_componente' en reglas_negocio.json para activarlo.")]
    if not _llm_disponible():
        return [_h("Proposito", "info",
                   f"[{c.nombre}] verificacion de proposito no ejecutada: falta OPENAI_API_KEY")]
    try:
        r = _juzgar_proposito(prompt, proposito)
    except Exception as exc:
        return [_h("Proposito", "info", f"[{c.nombre}] error: {type(exc).__name__}: {exc}")]

    if cost_meter is not None and r.get("_model"):
        cost_meter.track(r["_model"], r.get("_prompt_tokens") or 0, r.get("_completion_tokens") or 0)

    score = r.get("score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        return [_h("Proposito", "info", f"[{c.nombre}] el juicio no devolvio un score valido")]

    if score >= _SUCCESS_THRESHOLD:
        return []

    severidad = "alto" if score < 0.3 else "medio"
    evidencia = (
        f"Entrada simulada: {r.get('entrada_simulada', '')[:200]} | "
        f"Respuesta simulada: {r.get('respuesta_simulada', '')[:200]}"
    )
    return [_h(
        "Proposito", severidad,
        f"[{c.nombre}] el prompt probablemente NO cumple su proposito declarado (score {score:.2f})",
        evidencia,
        r.get("razon") or "Revisar el prompt contra el proposito declarado y ajustar instrucciones.",
    )]
