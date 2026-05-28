"""Historial de evaluaciones por agente.

Persiste cada run en outputs/history/{agent_id}.json y permite comparar
con el run anterior para detectar regresiones o mejoras.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


_HISTORY_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "outputs", "history"
)


def _history_path(agent_id: str) -> str:
    safe_id = agent_id.replace("/", "_").replace("\\", "_")
    return os.path.join(_HISTORY_DIR, f"{safe_id}.json")


def _ensure_dir() -> None:
    os.makedirs(_HISTORY_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Snapshot — datos que se guardan por run
# ---------------------------------------------------------------------------

def build_snapshot(
    agent_id: str,
    agent_name: str,
    scores: Dict[str, Any],
    analisis: Dict[str, Any],
    juez_report: Optional[Any] = None,
) -> Dict[str, Any]:
    """Construye el snapshot de una evaluación para persistir."""
    problemas = analisis.get("problemas", [])

    contra: Dict[str, Any] = {}
    if juez_report:
        resultados = getattr(juez_report, "results", [])
        total = len(resultados)
        passed = sum(1 for r in resultados if getattr(r, "passed", False))
        by_cat: Dict[str, Dict[str, int]] = {}
        for r in resultados:
            cat = getattr(r, "category", "unknown")
            if cat not in by_cat:
                by_cat[cat] = {"total": 0, "passed": 0}
            by_cat[cat]["total"] += 1
            if getattr(r, "passed", False):
                by_cat[cat]["passed"] += 1
        contra = {
            "total": total,
            "passed": passed,
            "pass_rate": round(passed / total, 3) if total else 0.0,
            "by_category": by_cat,
        }

    return {
        "timestamp": datetime.now().isoformat(),
        "juez_version": scores.get("juez_version", 1),
        "agent_name": agent_name,
        "score_general": round(scores.get("score_general", 0.0), 1),
        "dimensiones": {
            "seguridad":          round(scores.get("seguridad", 0.0), 1),
            "tools_integraciones": round(scores.get("tools_integraciones") or 0.0, 1),
            "observabilidad":     round(scores.get("observabilidad", 0.0), 1),
            "calidad_prompt":     round(scores.get("calidad_prompt", 0.0), 1),
            "mantenibilidad":     round(scores.get("mantenibilidad", 0.0), 1),
            "artefacto":          round(scores.get("artefacto") or 0.0, 1),
            "evaluacion_viva":    round(scores.get("evaluacion_viva") or 0.0, 1),
            "config_voz":         round(scores.get("config_voz", 0.0), 1),
        },
        "problemas": {
            "CRITICO": sum(1 for p in problemas if p.get("severidad") == "CRITICO"),
            "ALTO":    sum(1 for p in problemas if p.get("severidad") == "ALTO"),
            "MEDIO":   sum(1 for p in problemas if p.get("severidad") == "MEDIO"),
            "BAJO":    sum(1 for p in problemas if p.get("severidad") == "BAJO"),
        },
        "contra_agente": contra,
    }


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------

def guardar(agent_id: str, snapshot: Dict[str, Any]) -> None:
    """Agrega el snapshot al historial del agente."""
    _ensure_dir()
    path = _history_path(agent_id)
    history: Dict[str, Any] = {"agent_id": agent_id, "runs": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            pass
    history.setdefault("runs", []).append(snapshot)
    # Mantener máximo 50 runs
    history["runs"] = history["runs"][-50:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def cargar_anterior(agent_id: str) -> Optional[Dict[str, Any]]:
    """Retorna el snapshot previo más reciente de la MISMA juez_version, o None.

    Snapshots de versiones distintas no se comparan: las dimensiones y pesos
    pueden haber cambiado y la comparación sería engañosa.
    """
    path = _history_path(agent_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            history = json.load(f)
        runs = history.get("runs", [])
        if len(runs) < 2:
            return None
        actual = runs[-1]
        version_actual = actual.get("juez_version", 1)
        for prev in reversed(runs[:-1]):
            if prev.get("juez_version", 1) == version_actual:
                return prev
        return None
    except Exception:
        return None


def cargar_todos(agent_id: str) -> List[Dict[str, Any]]:
    """Retorna todos los runs del agente, del más antiguo al más reciente."""
    path = _history_path(agent_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            history = json.load(f)
        return history.get("runs", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Comparación y reporte de cambios
# ---------------------------------------------------------------------------

def _delta_str(actual: float, anterior: float) -> Tuple[str, str]:
    """Retorna (símbolo, cadena de delta) para una dimensión."""
    diff = actual - anterior
    if abs(diff) < 0.5:
        return "  ", f"= {actual:.1f}%"
    elif diff > 0:
        return "▲", f"▲ {actual:.1f}% (+{diff:.1f})"
    else:
        return "▼", f"▼ {actual:.1f}% ({diff:.1f})"


def generar_seccion_comparacion(
    actual: Dict[str, Any],
    anterior: Optional[Dict[str, Any]],
) -> str:
    """Genera la sección de texto del reporte comparando actual vs anterior."""
    lineas: List[str] = []

    def L(txt: str = "", indent: int = 2) -> None:
        lineas.append(" " * indent + txt)

    lineas.append("")
    lineas.append("--- 0. COMPARACION CON EVALUACION ANTERIOR " + "-" * 36)
    lineas.append("")

    if anterior is None:
        L("Primera evaluacion registrada — no hay historial previo para comparar.")
        L(f"Score actual: {actual['score_general']}%")
        lineas.append("")
        return "\n".join(lineas)

    ts_anterior = anterior.get("timestamp", "")[:16].replace("T", " ")
    score_actual = actual["score_general"]
    score_anterior = anterior["score_general"]
    diff_general = score_actual - score_anterior

    # Veredicto general
    if abs(diff_general) < 0.5:
        veredicto = "Sin cambios significativos respecto a la evaluacion anterior."
        simbolo = "="
    elif diff_general > 0:
        veredicto = f"MEJORA de {diff_general:+.1f} puntos respecto a la evaluacion anterior."
        simbolo = "▲"
    else:
        veredicto = f"REGRESION de {diff_general:.1f} puntos respecto a la evaluacion anterior."
        simbolo = "▼"

    L(f"Evaluacion anterior : {ts_anterior}")
    L(f"Score anterior      : {score_anterior}%")
    L(f"Score actual        : {score_actual}%  {simbolo}")
    L(f"Veredicto           : {veredicto}")
    lineas.append("")

    # Tabla de dimensiones
    dims_actual   = actual.get("dimensiones", {})
    dims_anterior = anterior.get("dimensiones", {})
    _LABELS = {
        "seguridad":          "Seguridad",
        "tools_integraciones":"Tools & Webhooks",
        "observabilidad":     "Observabilidad",
        "calidad_prompt":     "Calidad del Prompt",
        "mantenibilidad":     "Mantenibilidad",
        "artefacto":          "QA de Artefacto",
        "evaluacion_viva":    "Evaluacion en Vivo",
        "config_voz":         "Configuracion de Voz",
    }

    tiene_cambios_dim = False
    dim_lineas: List[str] = []
    for key, label in _LABELS.items():
        v_act = dims_actual.get(key, 0.0)
        v_ant = dims_anterior.get(key, 0.0)
        if v_act == 0.0 and v_ant == 0.0:
            continue
        _, delta = _delta_str(v_act, v_ant)
        diff = v_act - v_ant
        if abs(diff) >= 0.5:
            tiene_cambios_dim = True
        marker = "  " if abs(diff) < 0.5 else ("▲" if diff > 0 else "▼")
        dim_lineas.append(f"    {marker} {label:<25} {delta}")

    if dim_lineas:
        L("Cambios por dimension:")
        lineas.extend(dim_lineas)
        lineas.append("")

    # Problemas
    prob_actual   = actual.get("problemas", {})
    prob_anterior = anterior.get("problemas", {})
    for sev in ("CRITICO", "ALTO", "MEDIO"):
        v_act = prob_actual.get(sev, 0)
        v_ant = prob_anterior.get(sev, 0)
        diff = v_act - v_ant
        if diff != 0:
            accion = "nuevo" if diff > 0 else "resuelto"
            L(f"Problemas {sev}: {v_ant} → {v_act}  ({'+' if diff>0 else ''}{diff} {accion}{'s' if abs(diff)>1 else ''})")

    # Contra-agente
    ca_actual   = actual.get("contra_agente", {})
    ca_anterior = anterior.get("contra_agente", {})
    if ca_actual and ca_anterior:
        pr_act = ca_actual.get("pass_rate", 0.0)
        pr_ant = ca_anterior.get("pass_rate", 0.0)
        diff_pr = pr_act - pr_ant
        if abs(diff_pr) >= 0.02:
            s = f"{pr_act*100:.0f}% ({'+' if diff_pr>0 else ''}{diff_pr*100:.0f}pp)"
            marker = "▲" if diff_pr > 0 else "▼"
            L(f"{marker} Pass rate en vivo: {pr_ant*100:.0f}% → {s}")

        # Categorías que cambiaron
        cats_act = ca_actual.get("by_category", {})
        cats_ant = ca_anterior.get("by_category", {})
        all_cats = set(cats_act) | set(cats_ant)
        for cat in sorted(all_cats):
            pa = cats_act.get(cat, {})
            pp = cats_ant.get(cat, {})
            rate_act = pa.get("passed", 0) / max(pa.get("total", 1), 1)
            rate_ant = pp.get("passed", 0) / max(pp.get("total", 1), 1)
            if abs(rate_act - rate_ant) >= 0.3:
                m = "▲" if rate_act > rate_ant else "▼"
                L(f"  {m} {cat}: {rate_ant*100:.0f}% → {rate_act*100:.0f}%")

    lineas.append("")
    return "\n".join(lineas)
