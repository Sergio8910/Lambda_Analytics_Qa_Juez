"""Evaluación 24/7 reactiva: recibe un fallo de n8n y genera el reporte.

Pensado para el Error Workflow de n8n (errorTrigger): cuando un flujo falla,
n8n hace POST del payload del error al Juez. Aquí extraemos qué flujo falló y en
qué nodo, descargamos su JSON, corremos el análisis estático (sin disparar nada)
+ el QA de artefacto sintético, y generamos un reporte TXT con el contexto del
fallo al frente.

Aditivo: reusa `analyze_workflow`, `run_artifact_eval` y el guardado de reportes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_L = "=" * 70


def extract_failure_info(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrae los campos relevantes del payload del errorTrigger de n8n.

    Tolerante: el shape puede variar entre versiones de n8n.
    """
    payload = payload or {}
    execu = payload.get("execution", {}) or {}
    wf = payload.get("workflow", {}) or {}
    err = execu.get("error", {}) or {}
    if isinstance(err, str):
        err = {"message": err}
    node = err.get("node", {}) or {}
    node_name = node.get("name") if isinstance(node, dict) else None

    return {
        "workflow_id": wf.get("id") or payload.get("workflow_id"),
        "workflow_name": wf.get("name") or payload.get("workflow_name") or "(desconocido)",
        "error_message": err.get("message") or execu.get("error") or "(sin mensaje)",
        "error_description": err.get("description") or "",
        "failed_node": node_name or execu.get("lastNodeExecuted") or "(desconocido)",
        "execution_id": execu.get("id"),
        "execution_url": execu.get("url"),
        "mode": execu.get("mode"),
    }


def _fetch_workflow(workflow_id: str) -> Optional[Dict[str, Any]]:
    base = os.getenv("N8N_BASE_URL", "")
    key = os.getenv("N8N_API_KEY", "")
    if not (base and key and workflow_id):
        return None
    try:
        from juez.evaluar_n8n import _descargar_workflow_n8n
        return _descargar_workflow_n8n(base, key, str(workflow_id))
    except Exception:
        return None


def _slug(nombre: str) -> str:
    return nombre.lower().replace(" ", "_")[:40]


def render_failure_report(info: Dict[str, Any], analysis: Any, artef: Dict[str, Any], ts_iso: str) -> str:
    L: List[str] = [
        _L,
        "  EVALUACIÓN 24/7 — FALLO DETECTADO EN UN FLUJO n8n",
        "  Lambda Analytics — Juez",
        _L,
        f"  Detectado (UTC)    : {ts_iso}",
        f"  Flujo              : {info['workflow_name']}  (id {info.get('workflow_id')})",
        f"  Nodo que falló     : {info['failed_node']}",
        f"  Error              : {info['error_message']}",
    ]
    if info.get("error_description"):
        L.append(f"  Detalle            : {info['error_description']}")
    if info.get("execution_url"):
        L.append(f"  Ejecución          : {info['execution_url']}")
    elif info.get("execution_id"):
        L.append(f"  Ejecución          : id {info['execution_id']}")
    L.append(_L)

    if analysis is None:
        L.append("")
        L.append("  No se pudo descargar el flujo para análisis (¿falta N8N_API_KEY/BASE_URL,")
        L.append("  o el payload no traía workflow.id?). Se reporta solo el contexto del fallo.")
        L.append(_L)
        return "\n".join(L)

    sc = analysis.scorecard
    L.append("")
    L.append("  ANÁLISIS DEL FLUJO (estático, sin disparar)")
    L.append(f"     Estado             : {sc.status.upper()}   Score global: {sc.overall}")
    L.append(f"     Integridad         : {sc.workflow_integrity}")
    L.append(f"     Seguridad          : {sc.security_posture}")
    L.append(f"     Resiliencia oper.  : {sc.operational_resilience}")
    L.append(f"     Mantenibilidad     : {sc.maintainability}")
    L.append(f"     Redundancia        : {sc.redundancy}")

    if analysis.counts_by_severity:
        resumen = ", ".join(f"{k}={v}" for k, v in analysis.counts_by_severity.items())
        L.append(f"     Hallazgos          : {resumen}")

    # Top hallazgos (prioriza por severidad)
    orden = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings = sorted(analysis.findings, key=lambda f: orden.get(f.severity, 9))
    if findings:
        L.append("")
        L.append("  Hallazgos principales:")
        for f in findings[:12]:
            nodos = f" [{', '.join(f.node_names)}]" if f.node_names else ""
            L.append(f"     [{f.severity.upper()}] {f.title}{nodos}")
            if f.recommendation:
                L.append(f"            → {f.recommendation}")

    diag = getattr(analysis, "diagnosis", None)
    if diag is not None:
        L.append("")
        L.append("  Diagnóstico (IA):")
        L.append(f"     Veredicto: {diag.verdict}  (riesgo {diag.risk_level})")
        if diag.executive_summary:
            L.append(f"     {diag.executive_summary}")

    if artef and not artef.get("error"):
        L.append("")
        L.append(f"  QA DE ARTEFACTO/PDF (sintético): score {artef.get('score_artefacto', 0.0)}/100")
        for p in artef.get("problemas", []):
            L.append(f"     [{p.get('severidad')}] {p.get('descripcion')}")

    L.append(_L)
    return "\n".join(L)


def run_on_failure(
    payload: Dict[str, Any],
    *,
    with_diagnosis: bool = True,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """Procesa un fallo de n8n y genera el reporte de evaluación.

    Returns dict con: workflow_name, failed_node, error_message, status,
    reporte_path, score (si se pudo analizar).
    """
    def progress(msg: str, pct: int) -> None:
        if progress_cb:
            try:
                progress_cb(msg, pct)
            except Exception:
                pass

    info = extract_failure_info(payload)
    ts_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    progress("Descargando el flujo que falló", 20)
    # El payload puede traer el workflow completo; si no, lo bajamos por API.
    wf_json = None
    wf_in = payload.get("workflow") if isinstance(payload, dict) else None
    if isinstance(wf_in, dict) and wf_in.get("nodes"):
        wf_json = wf_in
    if wf_json is None:
        wf_json = _fetch_workflow(info.get("workflow_id"))

    analysis = None
    artef: Dict[str, Any] = {}
    if wf_json:
        progress("Analizando el flujo", 50)
        try:
            if with_diagnosis and os.getenv("OPENAI_API_KEY"):
                from juez.evaluation.n8n import analyze_workflow_with_diagnosis
                analysis, _warnings = analyze_workflow_with_diagnosis(wf_json)
            else:
                from juez.evaluation.n8n import analyze_workflow
                analysis = analyze_workflow(wf_json)
        except Exception:
            from juez.evaluation.n8n import analyze_workflow
            analysis = analyze_workflow(wf_json)

        progress("QA de artefacto (sintético)", 75)
        try:
            from juez.evaluation.artifact import run_artifact_eval
            artef = run_artifact_eval(_slug(info["workflow_name"])) or {}
        except Exception as exc:
            artef = {"error": str(exc)}

    progress("Generando reporte", 90)
    reporte = render_failure_report(info, analysis, artef, ts_iso)

    reporte_path = None
    try:
        from juez.api.runner import _guardar_reporte_txt
        reporte_path = _guardar_reporte_txt(
            info["workflow_name"] or "fallo", reporte, "fallo"
        )
    except Exception:
        pass

    return {
        "status": "done",
        "workflow_name": info["workflow_name"],
        "workflow_id": info.get("workflow_id"),
        "failed_node": info["failed_node"],
        "error_message": info["error_message"],
        "execution_url": info.get("execution_url"),
        "analizado": analysis is not None,
        "score": (analysis.scorecard.overall if analysis is not None else None),
        "reporte_path": reporte_path,
        "reporte_txt": reporte,
        "detectado_utc": ts_iso,
    }
