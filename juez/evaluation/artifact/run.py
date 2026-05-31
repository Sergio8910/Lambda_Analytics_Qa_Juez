"""Orquestador del QA de artefactos.

run_artifact_eval(agent_id, env) ->
    {} si el agente no tiene spec (no-op silencioso), o
    {score_artefacto, problemas, reporte, por_evaluador, agent_id}
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import specs as _specs
from .protocol import ArtifactContext, Issue
from .registry import make_driver, make_evaluator
from .report import generar_reporte_artefacto

_ENV_KEYS = ("N8N_BASE_URL", "N8N_API_KEY", "OPENAI_API_KEY",
             "ABAT_DB_URL", "GOOGLE_OAUTH_TOKEN")


def _env_snapshot(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {k: os.getenv(k, "") for k in _ENV_KEYS}
    if extra:
        env.update({k: v for k, v in extra.items() if v})
    return env


def run_artifact_eval(
    agent_id: str,
    env: Optional[Dict[str, str]] = None,
    spec_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    spec = spec_override or _specs.load_spec(agent_id)
    if not spec:
        return {}  # agente sin QA de artefacto configurado -> no-op

    env_full = _env_snapshot(env)

    # Auth para n8n si el driver lo necesita
    auth_headers = {}
    if env_full.get("N8N_API_KEY"):
        auth_headers["X-N8N-API-KEY"] = env_full["N8N_API_KEY"]

    drv_cfg = dict(spec.get("driver", {}).get("config", {}))
    drv_cfg.setdefault("base_url", env_full.get("N8N_BASE_URL", ""))
    # Solo pasar auth si el flujo es protegido; por defecto los webhooks n8n son publicos
    if spec.get("driver", {}).get("usar_auth"):
        drv_cfg.setdefault("auth_headers", auth_headers)
    driver = make_driver(spec["driver"]["type"], **drv_cfg)

    synthetic_input = spec.get("synthetic_input", {})
    trigger_result = driver.trigger(synthetic_input)

    por_evaluador: List[Dict[str, Any]] = []
    problemas: List[Issue] = []
    scores: List[float] = []

    for ev_spec in spec.get("evaluators", []):
        ctx: ArtifactContext = {
            "agent_id": agent_id,
            "spec": spec,
            "synthetic_input": synthetic_input,
            "trigger_result": trigger_result,
            "env": env_full,
        }
        ev = make_evaluator(ev_spec["type"], **ev_spec.get("config", {}))
        res = ev.evaluate(ctx)
        por_evaluador.append(res)
        problemas.extend(res.get("problemas", []))
        scores.append(res.get("score", 0.0))

    agregado = {
        "agent_id": agent_id,
        "score_artefacto": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "problemas": problemas,
        "por_evaluador": por_evaluador,
        "trigger_result": trigger_result,
    }
    agregado["reporte"] = generar_reporte_artefacto(agregado)
    return agregado
