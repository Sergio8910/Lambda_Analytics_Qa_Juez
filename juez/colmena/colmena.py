"""Núcleo de La Colmena. Orquesta obreras (reusan el Juez) sobre un proyecto."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# Penalización por severidad para el score del proyecto.
_PESO = {"critico": 25, "alto": 10, "medio": 4, "bajo": 1, "info": 0}
_SEV_NORM = {
    "critical": "critico", "critico": "critico", "crítica": "critico", "critica": "critico",
    "high": "alto", "alto": "alto", "medium": "medio", "medio": "medio",
    "low": "bajo", "bajo": "bajo", "info": "info",
}
# Obreras dinámicas: disparan / cuestan tokens -> opt-in.
_OBRERAS_DINAMICAS = ("Performance", "Exploradora (adversarial)", "Niñera (edge cases)")


def _norm_sev(s: Any) -> str:
    return _SEV_NORM.get(str(s or "").strip().lower(), "medio")


class Componente(BaseModel):
    """Una pieza del proyecto a evaluar."""
    kind: str = Field(..., description="'n8n' | 'prompt'")
    nombre: str
    workflow_json: Optional[Dict[str, Any]] = None
    workflow_id: Optional[str] = None
    objetivos: List[Dict[str, Any]] = Field(default_factory=list)
    prompt: Optional[str] = None
    model_config = {"extra": "allow"}


class ColmenaResult(BaseModel):
    project_id: str
    score: float
    veredicto: str  # "LISTO" | "NECESITA TRABAJO"
    resumen_severidad: Dict[str, int] = Field(default_factory=dict)
    hallazgos: List[Dict[str, Any]] = Field(default_factory=list)
    obreras_no_ejecutadas: List[str] = Field(default_factory=list)
    componentes: int = 0
    model_config = {"extra": "forbid"}


def _h(obrera: str, severidad: str, descripcion: str, ubicacion: str = "", accion: str = "") -> Dict[str, Any]:
    return {"obrera": obrera, "severidad": _norm_sev(severidad),
            "descripcion": descripcion, "ubicacion": ubicacion, "accion": accion}


# --------------------------------------------------------------------------- obreras (reusan el Juez)
def _resolver_wf(c: Componente) -> Optional[Dict[str, Any]]:
    if c.workflow_json:
        return c.workflow_json
    if c.workflow_id:
        try:
            from juez.evaluar_n8n import _descargar_workflow_n8n
            return _descargar_workflow_n8n(os.getenv("N8N_BASE_URL", ""), os.getenv("N8N_API_KEY", ""), c.workflow_id)
        except Exception:
            return None
    return None


def _guardiana(c: Componente) -> List[Dict[str, Any]]:
    wf = _resolver_wf(c)
    if not wf:
        return []
    from juez.evaluation.static_checks import check_tool_security
    return [_h("Guardiana (seguridad)", p.get("severidad"), f"[{c.nombre}] {p.get('descripcion')}",
               p.get("nodo", ""), "Revisar/mitigar el riesgo de seguridad")
            for p in check_tool_security(wf)]


def _flujos(c: Componente) -> List[Dict[str, Any]]:
    wf = _resolver_wf(c)
    if not wf:
        return []
    from juez.evaluation.n8n import analyze_workflow
    a = analyze_workflow(wf)
    return [_h("Flujos", f.severity, f"[{c.nombre}] {f.title}",
               ", ".join(f.node_names), f.recommendation) for f in a.findings]


def _integracion(c: Componente) -> List[Dict[str, Any]]:
    wf = _resolver_wf(c)
    if not wf or not c.objetivos:
        return []
    from juez.evaluation.n8n import Objective, verify_objectives
    rep = verify_objectives(wf, [Objective(**o) for o in c.objetivos])
    return [_h("Integración", f.severity, f"[{c.nombre}] {f.title}",
               ", ".join(f.node_names), f.recommendation) for f in rep.findings]


def _prompts(c: Componente) -> List[Dict[str, Any]]:
    if not c.prompt:
        return []
    from prompt_eval.evaluator import evaluate_prompt
    from prompt_eval.models import PromptEvalRequest
    res = evaluate_prompt(PromptEvalRequest(prompt=c.prompt, nombre=c.nombre, incluir_llm_judge=False))
    if res.veredicto in ("excelente", "bueno"):
        return []
    sev = "alto" if res.veredicto in ("deficiente", "critico") else "medio"
    accion = "; ".join(res.top_recomendaciones[:3]) if getattr(res, "top_recomendaciones", None) else ""
    return [_h("Prompts", sev, f"[{c.nombre}] prompt {res.veredicto} (score {res.score_global})",
               c.nombre, accion)]


_OBRERAS_ESTATICAS = {
    "n8n": [_guardiana, _flujos, _integracion],
    "prompt": [_prompts],
}


def _obreras_dinamicas():
    from .obreras_dinamicas import exploradora, ninera, performance
    return [exploradora, ninera, performance]


# --------------------------------------------------------------------------- Reina (orquesta)
def run_colmena(project_id: str, componentes: List[Componente], incluir_dinamicas: bool = True) -> ColmenaResult:
    """Corre TODAS las obreras (estáticas + dinámicas) EN PARALELO sobre los componentes."""
    dinamicas = _obreras_dinamicas() if incluir_dinamicas else []
    tareas = []
    for c in componentes:
        for obrera in _OBRERAS_ESTATICAS.get(c.kind, []):
            tareas.append((obrera, c))
        for obrera in dinamicas:
            tareas.append((obrera, c))

    hallazgos: List[Dict[str, Any]] = []
    if tareas:
        with ThreadPoolExecutor(max_workers=min(8, len(tareas))) as ex:
            for res in ex.map(lambda t: _safe(t[0], t[1]), tareas):
                hallazgos.extend(res)

    # Consolidación
    resumen = {k: 0 for k in _PESO}
    for h in hallazgos:
        resumen[h["severidad"]] = resumen.get(h["severidad"], 0) + 1
    score = max(0.0, 100.0 - sum(_PESO.get(sev, 0) * n for sev, n in resumen.items()))
    listo = resumen.get("critico", 0) == 0 and resumen.get("alto", 0) == 0
    orden = {"critico": 0, "alto": 1, "medio": 2, "bajo": 3, "info": 4}
    hallazgos.sort(key=lambda h: orden.get(h["severidad"], 9))

    return ColmenaResult(
        project_id=project_id,
        score=round(score, 1),
        veredicto="LISTO" if listo else "NECESITA TRABAJO",
        resumen_severidad=resumen,
        hallazgos=hallazgos,
        obreras_no_ejecutadas=[] if incluir_dinamicas else list(_OBRERAS_DINAMICAS),
        componentes=len(componentes),
    )


def _safe(fn, c: Componente) -> List[Dict[str, Any]]:
    try:
        return fn(c)
    except Exception as exc:
        return [_h("Colmena", "info", f"[{c.nombre}] obrera {fn.__name__} falló: {type(exc).__name__}: {exc}")]


# --------------------------------------------------------------------------- reporte
_L = "=" * 80
_ICON = {"critico": "[CRÍTICO]", "alto": "[ALTO]   ", "medio": "[MEDIO]  ", "bajo": "[BAJO]   ", "info": "[INFO]   "}


def render_colmena_report(r: ColmenaResult) -> str:
    L = [_L, "  LA COLMENA - REPORTE DE PROYECTO", "  Lambda Analytics - Juez", _L]
    estado = r.veredicto  # texto plano (sin emojis: robusto en cualquier consola)
    L.append(f"  Proyecto           : {r.project_id}")
    L.append(f"  Componentes        : {r.componentes}")
    L.append(f"  Estado general     : {estado}  (score {r.score}/100)")
    L.append(f"  Hallazgos          : " + ", ".join(f"{k}={v}" for k, v in r.resumen_severidad.items() if v))
    if r.obreras_no_ejecutadas:
        L.append(f"  Obreras NO corridas: {', '.join(r.obreras_no_ejecutadas)} (dinámicas, opt-in)")
    L.append(_L)
    if r.hallazgos:
        L.append("  HALLAZGOS CONSOLIDADOS (más grave primero):")
        for h in r.hallazgos:
            L.append(f"  {_ICON.get(h['severidad'], '[?]')} {h['obrera']}: {h['descripcion']}")
            if h.get("ubicacion"):
                L.append(f"            ubicación: {h['ubicacion']}")
            if h.get("accion"):
                L.append(f"            acción: {h['accion']}")
    else:
        L.append("  Sin hallazgos en las obreras estáticas.")
    L.append(_L)
    return "\n".join(L)
