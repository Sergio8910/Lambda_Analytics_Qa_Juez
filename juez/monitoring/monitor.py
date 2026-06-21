"""Núcleo del monitoreo programado.

Una "pasada" (pass) evalúa todos los targets configurados y guarda un reporte
TXT por target en outputs/monitoring/<slug>/<timestamp>.txt, más un resumen.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

_L = "=" * 70


class MonitorTarget(BaseModel):
    """Un objetivo de monitoreo."""

    kind: str = Field(..., description="'n8n' | 'prompt'")
    name: str = Field(..., description="Nombre legible (se usa para la carpeta de reportes)")
    # n8n
    workflow_id: Optional[str] = Field(None, description="ID o URL del flujo n8n (kind=n8n)")
    objetivos: List[Dict[str, Any]] = Field(default_factory=list, description="Objetivos a verificar (kind=n8n)")
    mode: str = Field("lightweight", description="'lightweight' (estático, no dispara) | 'full' (dispara)")
    # prompt
    prompt: Optional[str] = Field(None, description="System prompt a evaluar (kind=prompt)")

    model_config = {"extra": "allow"}


class MonitorConfig(BaseModel):
    interval_seconds: int = Field(3600, ge=30, description="Intervalo del modo loop")
    output_dir: str = Field("outputs/monitoring", description="Dónde guardar los reportes")
    targets: List[MonitorTarget] = Field(default_factory=list)

    model_config = {"extra": "allow"}


def load_config(path: str) -> MonitorConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MonitorConfig(**data)


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (s or "target"))[:50]


# --------------------------------------------------------------------------- n8n
def _evaluate_n8n(target: MonitorTarget) -> Dict[str, Any]:
    from juez.evaluation.n8n import (
        Objective,
        analyze_workflow,
        verify_objectives,
    )

    base = os.getenv("N8N_BASE_URL", "")
    key = os.getenv("N8N_API_KEY", "")
    from juez.evaluar_n8n import _descargar_workflow_n8n, _parsear_url_workflow

    wid = target.workflow_id or ""
    if "/" in wid or "http" in wid:
        _base, wid = _parsear_url_workflow(wid)
    wf = _descargar_workflow_n8n(base, key, wid)

    analysis = analyze_workflow(wf)

    obj_report = None
    if target.objetivos:
        objs = [Objective(**o) for o in target.objetivos]
        obj_report = verify_objectives(wf, objs)

    artef = {}
    try:
        from juez.evaluation.artifact import run_artifact_eval
        artef = run_artifact_eval(_slug(target.name).lower()) or {}
    except Exception as exc:
        artef = {"error": str(exc)}

    # Seguridad de tools (código peligroso, SSRF, exfiltración, prompt injection…)
    seguridad = []
    try:
        from juez.evaluation.static_checks import check_tool_security
        seguridad = check_tool_security(wf)
    except Exception:
        seguridad = []

    score = round(analysis.scorecard.overall * 100, 1)
    return {
        "score": score,
        "estado": analysis.scorecard.status,
        "analysis": analysis,
        "objetivos": obj_report,
        "artefacto": artef,
        "seguridad": seguridad,
        "workflow_name": wf.get("name", target.name),
    }


def _render_n8n(target: MonitorTarget, res: Dict[str, Any], ts_iso: str) -> str:
    a = res["analysis"]
    L = [_L, f"  MONITOREO — {target.name}", "  Lambda Analytics — Juez", _L]
    L.append(f"  Timestamp (UTC)    : {ts_iso}")
    L.append(f"  Tipo               : flujo n8n  ({res.get('workflow_name')})")
    L.append(f"  Estado             : {a.scorecard.status.upper()}   Score: {res['score']}/100")
    if a.counts_by_severity:
        L.append(f"  Hallazgos          : " + ", ".join(f"{k}={v}" for k, v in a.counts_by_severity.items()))

    orden = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    for f in sorted(a.findings, key=lambda x: orden.get(x.severity, 9))[:10]:
        nodos = f" [{', '.join(f.node_names)}]" if f.node_names else ""
        L.append(f"     [{f.severity.upper()}] {f.title}{nodos}")

    obj = res.get("objetivos")
    if obj is not None:
        L.append("")
        L.append(f"  Objetivos          : {obj.veredicto.upper()} ({obj.cumplidos} ok / {obj.parciales} parc / {obj.incumplidos} incum)")
        for o in obj.objetivos:
            L.append(f"     [{o.status}] {o.id}")

    artef = res.get("artefacto") or {}
    if artef and not artef.get("error"):
        L.append("")
        L.append(f"  QA de PDF (sintético): score {artef.get('score_artefacto')}/100, problemas {len(artef.get('problemas', []))}")

    seguridad = res.get("seguridad") or []
    if seguridad:
        L.append("")
        L.append(f"  Seguridad de tools  : {len(seguridad)} hallazgo(s)")
        for s in seguridad[:8]:
            L.append(f"     [{s.get('severidad')}] {s.get('tipo')}: {s.get('descripcion')} ({s.get('nodo')})")
    L.append(_L)
    return "\n".join(L)


# --------------------------------------------------------------------------- prompt
def _evaluate_prompt(target: MonitorTarget) -> Dict[str, Any]:
    from prompt_eval.evaluator import evaluate_prompt
    from prompt_eval.models import PromptEvalRequest

    res = evaluate_prompt(PromptEvalRequest(prompt=target.prompt or "", nombre=target.name))
    return {"score": res.score_global, "estado": res.veredicto, "result": res}


def _render_prompt(target: MonitorTarget, res: Dict[str, Any], ts_iso: str) -> str:
    r = res["result"]
    L = [_L, f"  MONITOREO — {target.name}", "  Lambda Analytics — Juez", _L]
    L.append(f"  Timestamp (UTC)    : {ts_iso}")
    L.append(f"  Tipo               : system prompt")
    L.append(f"  Veredicto          : {r.veredicto.upper()}   Score: {r.score_global}/100")
    L.append(_L)
    return "\n".join(L)


# --------------------------------------------------------------------------- orquestación
_EVALUADORES = {
    "n8n": (_evaluate_n8n, _render_n8n),
    "prompt": (_evaluate_prompt, _render_prompt),
}


def evaluate_target(target: MonitorTarget, output_dir: str, ts_iso: str) -> Dict[str, Any]:
    """Evalúa un target, guarda su reporte TXT y devuelve un resumen."""
    fns = _EVALUADORES.get(target.kind)
    if not fns:
        return {"name": target.name, "kind": target.kind, "status": "error",
                "error": f"kind no soportado: {target.kind}"}
    evaluar, render = fns
    try:
        res = evaluar(target)
        reporte = render(target, res, ts_iso)
        ts_file = ts_iso.replace(":", "").replace("-", "").replace("+0000", "")
        carpeta = Path(output_dir) / _slug(target.name)
        carpeta.mkdir(parents=True, exist_ok=True)
        path = carpeta / f"{_slug(target.name)}_{ts_file}.txt"
        path.write_text(reporte, encoding="utf-8")
        return {"name": target.name, "kind": target.kind, "status": "ok",
                "score": res.get("score"), "estado": res.get("estado"),
                "reporte_path": str(path)}
    except Exception as exc:
        return {"name": target.name, "kind": target.kind, "status": "error",
                "error": f"{type(exc).__name__}: {exc}"}


def run_monitoring_pass(config: MonitorConfig, ts_iso: Optional[str] = None) -> Dict[str, Any]:
    """Corre UNA pasada de monitoreo sobre todos los targets configurados."""
    ts_iso = ts_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    resultados = [evaluate_target(t, config.output_dir, ts_iso) for t in config.targets]

    ok = [r for r in resultados if r["status"] == "ok"]
    err = [r for r in resultados if r["status"] != "ok"]

    # Resumen de la pasada
    lineas = [_L, "  RESUMEN DE MONITOREO", f"  {ts_iso}", _L,
              f"  Targets: {len(resultados)}  (ok {len(ok)}, error {len(err)})", ""]
    for r in resultados:
        if r["status"] == "ok":
            lineas.append(f"  [OK]    {r['name']:<28} score {r.get('score')}/100 ({r.get('estado')})")
        else:
            lineas.append(f"  [ERROR] {r['name']:<28} {r.get('error')}")
    lineas.append(_L)
    resumen_txt = "\n".join(lineas)

    ts_file = ts_iso.replace(":", "").replace("-", "").replace("+0000", "")
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    resumen_path = out / f"_resumen_{ts_file}.txt"
    resumen_path.write_text(resumen_txt, encoding="utf-8")

    return {
        "timestamp": ts_iso,
        "total": len(resultados),
        "ok": len(ok),
        "error": len(err),
        "resultados": resultados,
        "resumen_path": str(resumen_path),
        "resumen_txt": resumen_txt,
    }
