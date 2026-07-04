"""Run diff entre corridas del contra-agente.

Persiste cada `BatchResult` en disco, recupera la corrida previa al actual
y genera un diff comparando score global, dimensiones del scorecard y
resultados por plan_id.

Layout en disco:
    out_dir/
      {agent_id}/
        20260530T101500Z.json
        20260531T093000Z.json
        ...

Ordenamiento por nombre de archivo (timestamp ISO compact) garantiza orden
cronologico estable sin depender del filesystem.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import BatchResult


_ESTABLE_THRESHOLD = 0.05
_REGRESION_DELTA_PTS = -10.0
_MEJORA_DELTA_PTS = 10.0


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def _timestamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")


def persistir_run(
    batch_result: BatchResult,
    agent_id: str,
    out_dir: str = "outputs/history",
) -> str:
    """Persiste un BatchResult en disco.

    Crea `out_dir/{agent_id}/` si no existe y guarda `{timestamp}.json` con
    el resultado serializado via `model_dump`.

    Returns:
        Ruta absoluta al archivo guardado.
    """
    target_dir = Path(out_dir) / agent_id
    target_dir.mkdir(parents=True, exist_ok=True)

    ts = _timestamp()
    path = target_dir / f"{ts}.json"

    # Garantizar nombre unico aunque dos persistencias ocurran en el mismo us
    while path.exists():
        ts = _timestamp() + "_x"
        path = target_dir / f"{ts}.json"

    data = batch_result.model_dump()
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2, default=str)

    return str(path.resolve())


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


def _listar_runs(agent_id: str, out_dir: str) -> List[Path]:
    target_dir = Path(out_dir) / agent_id
    if not target_dir.exists():
        return []
    files = sorted(p for p in target_dir.iterdir() if p.is_file() and p.suffix == ".json")
    return files


def cargar_run_previo(
    agent_id: str,
    out_dir: str = "outputs/history",
) -> Optional[Dict[str, Any]]:
    """Devuelve el penultimo run persistido (el "previo" al actual).

    Si solo hay uno o ninguno → None.
    """
    files = _listar_runs(agent_id, out_dir)
    if len(files) < 2:
        return None
    previo_path = files[-2]
    try:
        with previo_path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Helpers del diff
# ---------------------------------------------------------------------------


def _resumen_actual(actual: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agent_id": actual.get("agent_id"),
        "batch_id": actual.get("batch_id"),
        "total": actual.get("total"),
        "passed": actual.get("passed"),
        "failed": actual.get("failed"),
        "pass_rate": actual.get("pass_rate"),
        "scorecard": actual.get("scorecard"),
    }


def _label_score(delta: float) -> str:
    if abs(delta) < _ESTABLE_THRESHOLD:
        return "ESTABLE"
    if delta > 0:
        return "MEJORA"
    return "REGRESION"


def _index_resultados(d: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Indexa los results por plan_id."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in (d.get("results") or []):
        if not isinstance(r, dict):
            continue
        pid = r.get("plan_id")
        if pid:
            out[pid] = r
    return out


def _score_pct(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    # En el modelo, overall_score viene en [0,1]. Lo expresamos en puntos sobre 100.
    return v * 100.0


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def generar_diff(
    actual_dict: Dict[str, Any],
    previo_dict: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Genera un diff estructurado entre dos corridas serializadas.

    Args:
        actual_dict: BatchResult.model_dump() de la corrida actual.
        previo_dict: BatchResult.model_dump() de la corrida previa, o None.

    Returns:
        Dict con la forma documentada en el docstring del modulo.
    """
    if previo_dict is None:
        return {
            "primera_corrida": True,
            "actual": _resumen_actual(actual_dict or {}),
        }

    actual = actual_dict or {}
    previo = previo_dict or {}

    # Score global
    actual_pr = float(actual.get("pass_rate") or 0.0)
    previo_pr = float(previo.get("pass_rate") or 0.0)
    delta_pr = actual_pr - previo_pr
    global_score = {
        "actual": actual_pr,
        "previo": previo_pr,
        "delta": delta_pr,
        "label": _label_score(delta_pr),
    }

    # Dimensiones (scorecard) — solo dimensiones presentes en AMBOS
    sc_actual = actual.get("scorecard") or {}
    sc_previo = previo.get("scorecard") or {}
    dimensiones: Dict[str, Dict[str, float]] = {}
    if isinstance(sc_actual, dict) and isinstance(sc_previo, dict):
        for dim in sc_actual.keys():
            if dim in sc_previo:
                try:
                    a = float(sc_actual.get(dim) or 0.0)
                    p = float(sc_previo.get(dim) or 0.0)
                except (TypeError, ValueError):
                    continue
                dimensiones[dim] = {
                    "actual": a,
                    "previo": p,
                    "delta": a - p,
                }

    # Por plan_id
    idx_actual = _index_resultados(actual)
    idx_previo = _index_resultados(previo)

    plans_actual = set(idx_actual.keys())
    plans_previo = set(idx_previo.keys())
    plans_comunes = plans_actual & plans_previo

    regresiones: List[Dict[str, Any]] = []
    mejoras: List[Dict[str, Any]] = []
    for pid in sorted(plans_comunes):
        ra = idx_actual[pid]
        rp = idx_previo[pid]
        actual_score = _score_pct(ra.get("overall_score"))
        previo_score = _score_pct(rp.get("overall_score"))
        delta = actual_score - previo_score
        entry = {
            "plan_id": pid,
            "category": ra.get("category") or rp.get("category"),
            "previo_score": previo_score,
            "actual_score": actual_score,
            "delta": delta,
        }
        if delta <= _REGRESION_DELTA_PTS:
            regresiones.append(entry)
        elif delta >= _MEJORA_DELTA_PTS:
            mejoras.append(entry)

    casos_nuevos = sorted(plans_actual - plans_previo)
    casos_perdidos = sorted(plans_previo - plans_actual)

    return {
        "global_score": global_score,
        "dimensiones": dimensiones,
        "regresiones": regresiones,
        "mejoras": mejoras,
        "casos_nuevos": casos_nuevos,
        "casos_perdidos": casos_perdidos,
    }


__all__ = ["persistir_run", "cargar_run_previo", "generar_diff"]
