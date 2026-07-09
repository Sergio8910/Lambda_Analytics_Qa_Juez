"""Diagnostico LLM narrativo del contra-agente.

Toma un `BatchResult` y produce un diagnostico ejecutivo en lenguaje claro
siguiendo un template fijo (Veredicto, Fortalezas, Debilidades, Causa raiz,
Accion recomendada).

Modo de operacion:
    - Si `openai_key` viene vacio o falla la llamada al LLM: fallback
      heuristico que reconstruye el mismo template usando solo conteos.
    - Si `openai_key` viene seteado: arma un summary compacto del batch y
      pide a GPT que responda en el formato exacto.

NUNCA propaga excepciones al caller.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import BatchResult

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "Sos un evaluador senior de agentes conversacionales. Te paso el resumen "
    "de la corrida de pruebas de un agente y tenes que devolver un diagnostico "
    "ejecutivo. Respondes SIEMPRE en espanol y SIEMPRE respetando exactamente "
    "este template, sin agregar secciones extra ni cambiar los encabezados:\n\n"
    "## Veredicto ejecutivo\n"
    "[1 frase con la nota global del agente]\n\n"
    "## Fortalezas\n"
    "- [bullet 1]\n"
    "- [bullet 2]\n"
    "- [bullet 3]\n\n"
    "## Debilidades\n"
    "- [bullet 1 con evidencia concreta]\n"
    "- [bullet 2]\n"
    "- [bullet 3]\n\n"
    "## Causa raiz probable\n"
    "[1 parrafo conectando los puntos anteriores]\n\n"
    "## Accion recomendada de mayor impacto\n"
    "- [una sola accion concreta]\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _categorias_ordenadas(batch_result: BatchResult) -> List[Tuple[str, Dict[str, Any]]]:
    """Devuelve las categorias ordenadas por pass_rate ascendente (peor primero)."""
    by_cat = getattr(batch_result, "by_category", {}) or {}
    items: List[Tuple[str, Dict[str, Any]]] = []
    for cat, data in by_cat.items():
        if isinstance(data, dict):
            items.append((cat, data))
    items.sort(key=lambda kv: float(kv[1].get("pass_rate") or 0.0))
    return items


def _top_peores(batch_result: BatchResult, n: int = 3) -> List[Tuple[str, float, int, int]]:
    """Top N categorias con peor pass_rate (lista de tuplas cat, rate, passed, total)."""
    out: List[Tuple[str, float, int, int]] = []
    for cat, data in _categorias_ordenadas(batch_result)[:n]:
        out.append(
            (
                cat,
                float(data.get("pass_rate") or 0.0),
                int(data.get("passed") or 0),
                int(data.get("total") or 0),
            )
        )
    return out


def _top_mejores(batch_result: BatchResult, n: int = 3) -> List[Tuple[str, float, int, int]]:
    items = list(reversed(_categorias_ordenadas(batch_result)))[:n]
    return [
        (
            cat,
            float(data.get("pass_rate") or 0.0),
            int(data.get("passed") or 0),
            int(data.get("total") or 0),
        )
        for cat, data in items
    ]


def _verdict_e2e(batch_result: BatchResult) -> Optional[str]:
    """Si hay resultados con artifact_verdict, devuelve un resumen una linea."""
    results = getattr(batch_result, "results", []) or []
    artifact_cases = [r for r in results if getattr(r, "artifact_verdict", None)]
    if not artifact_cases:
        return None

    completados = 0
    skipped = 0
    scores: List[float] = []
    verdicts_ok = 0
    verdicts_fail = 0
    for r in artifact_cases:
        v = r.artifact_verdict or {}
        status = v.get("status")
        if status == "completed":
            completados += 1
            s = v.get("score")
            if s is not None:
                try:
                    scores.append(float(s))
                except (TypeError, ValueError):
                    pass
            verdict = (v.get("verdict") or "").upper()
            if verdict in ("OK", "PASS", "APROBADO"):
                verdicts_ok += 1
            elif verdict in ("FAIL", "RECHAZADO", "ERROR"):
                verdicts_fail += 1
        elif status == "skipped":
            skipped += 1

    avg_txt = ""
    if scores:
        avg = sum(scores) / len(scores)
        avg_txt = f", score promedio {avg:.0%}"
    return (
        f"e2e: {completados} completados ({verdicts_ok} OK / {verdicts_fail} FAIL), "
        f"{skipped} omitidos{avg_txt}"
    )


def _categoria_label(cat: str) -> str:
    return {
        "happy_path": "Flujo normal",
        "herramienta": "Uso de herramientas",
        "recorrido_completo": "Cobertura total de herramientas",
        "multi_turno": "Memoria multi-turno",
        "limite": "Limites de dominio",
        "caos": "Robustez ante caos",
        "agresivo": "Manejo de usuarios molestos",
        "seguridad": "Seguridad / manipulacion",
        "contexto_multiple": "Contexto ambiguo",
    }.get(cat, cat)


# ---------------------------------------------------------------------------
# Fallback heuristico
# ---------------------------------------------------------------------------


def _veredicto_text(pr: float) -> str:
    if pr >= 0.90:
        return "EXCELENTE"
    if pr >= 0.75:
        return "BUENO"
    if pr >= 0.60:
        return "REGULAR"
    if pr >= 0.40:
        return "DEFICIENTE"
    return "CRITICO"


def _fallback_diagnostico(batch_result: BatchResult) -> str:
    total = int(getattr(batch_result, "total", 0) or 0)
    passed = int(getattr(batch_result, "passed", 0) or 0)
    pr = float(getattr(batch_result, "pass_rate", 0.0) or 0.0)
    nivel = _veredicto_text(pr)

    if total == 0:
        veredicto = (
            "No hay conversaciones evaluadas en esta corrida; no es posible emitir un "
            "veredicto de fondo sobre el agente."
        )
    else:
        veredicto = (
            f"Nivel {nivel}: el agente paso {passed} de {total} conversaciones "
            f"({pr:.0%} de exito global)."
        )

    fortalezas = []
    for cat, rate, p, t in _top_mejores(batch_result, n=3):
        if t == 0:
            continue
        fortalezas.append(
            f"{_categoria_label(cat)}: {p}/{t} conversaciones aprobadas ({rate:.0%})."
        )
    while len(fortalezas) < 3:
        fortalezas.append("Sin evidencia adicional de fortalezas en esta corrida.")

    debilidades = []
    for cat, rate, p, t in _top_peores(batch_result, n=3):
        if t == 0:
            continue
        debilidades.append(
            f"{_categoria_label(cat)}: solo {p}/{t} aprobadas ({rate:.0%})."
        )
    while len(debilidades) < 3:
        debilidades.append("Sin evidencia adicional de debilidades en esta corrida.")

    causa_lines: List[str] = []
    peores = [c for c in _top_peores(batch_result, n=2) if c[3] > 0]
    if peores:
        nombres = " y ".join(_categoria_label(c[0]) for c in peores)
        causa_lines.append(
            f"Las categorias mas debiles ({nombres}) concentran la mayor parte de los "
            f"fallos del agente. "
        )
    else:
        causa_lines.append(
            "No hay un patron claro de fallo concentrado en categorias especificas. "
        )

    verdict_e2e = _verdict_e2e(batch_result)
    if verdict_e2e:
        causa_lines.append(f"En la verificacion de artefactos: {verdict_e2e}. ")

    causa_lines.append(
        "El nivel global sugiere que la calidad del prompt y/o el uso de "
        "herramientas son las palancas con mayor impacto."
    )
    causa = "".join(causa_lines).strip()

    recs = list(getattr(batch_result, "recommendations", []) or [])
    if recs:
        accion = recs[0]
    elif peores:
        cat_top = peores[0][0]
        accion = (
            f"Reforzar el prompt y los criterios de exito en la categoria "
            f"'{_categoria_label(cat_top)}' antes de la proxima corrida."
        )
    else:
        accion = (
            "Mantener la calidad actual y agregar mas casos adversariales en la "
            "proxima corrida para encontrar nuevos puntos debiles."
        )

    lines: List[str] = []
    lines.append("## Veredicto ejecutivo")
    lines.append(veredicto)
    lines.append("")
    lines.append("## Fortalezas")
    for f in fortalezas[:3]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Debilidades")
    for d in debilidades[:3]:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("## Causa raiz probable")
    lines.append(causa)
    lines.append("")
    lines.append("## Accion recomendada de mayor impacto")
    lines.append(f"- {accion}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary para el LLM
# ---------------------------------------------------------------------------


def _build_summary(batch_result: BatchResult) -> str:
    """Resumen compacto del batch para mandar al LLM."""
    total = int(getattr(batch_result, "total", 0) or 0)
    passed = int(getattr(batch_result, "passed", 0) or 0)
    failed = int(getattr(batch_result, "failed", 0) or 0)
    pr = float(getattr(batch_result, "pass_rate", 0.0) or 0.0)

    lines: List[str] = []
    lines.append(f"Agente: {getattr(batch_result, 'agent_id', 'desconocido')}")
    lines.append(f"Conversaciones: {total} (paso {passed}, fallo {failed})")
    lines.append(f"Pass rate global: {pr:.2%}")

    peores = _top_peores(batch_result, n=3)
    if peores:
        lines.append("")
        lines.append("Top categorias con peor desempeno:")
        for cat, rate, p, t in peores:
            lines.append(f"  - {_categoria_label(cat)} ({cat}): {p}/{t} ({rate:.0%})")

    mejores = _top_mejores(batch_result, n=3)
    if mejores:
        lines.append("")
        lines.append("Top categorias con mejor desempeno:")
        for cat, rate, p, t in mejores:
            lines.append(f"  - {_categoria_label(cat)} ({cat}): {p}/{t} ({rate:.0%})")

    verdict_e2e = _verdict_e2e(batch_result)
    if verdict_e2e:
        lines.append("")
        lines.append(f"Verificacion e2e de artefactos -> {verdict_e2e}")

    scorecard = getattr(batch_result, "scorecard", {}) or {}
    if scorecard:
        lines.append("")
        lines.append("Scorecard (metricas agregadas):")
        for metric, value in scorecard.items():
            try:
                v = float(value)
                lines.append(f"  - {metric}: {v:.2f}")
            except (TypeError, ValueError):
                lines.append(f"  - {metric}: {value}")

    recs = list(getattr(batch_result, "recommendations", []) or [])
    if recs:
        lines.append("")
        lines.append("Recomendaciones detectadas por el sistema:")
        for r in recs[:5]:
            lines.append(f"  - {r}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenAI client (importado lazy para que sea mockeable)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - solo para que el patch tenga un nombre que existe
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def analizar_diagnostico(
    batch_result: BatchResult,
    openai_key: str = "",
    model: str = "gpt-4o-mini",
) -> str:
    """Produce un diagnostico narrativo del batch.

    Args:
        batch_result: BatchResult del contra-agente.
        openai_key: API key de OpenAI. Si esta vacia, se usa fallback heuristico.
        model: modelo a usar via OpenAI.

    Returns:
        String con el diagnostico en formato markdown segun el template fijo.
        NUNCA levanta excepciones.
    """
    if not openai_key:
        return _fallback_diagnostico(batch_result)

    try:
        if OpenAI is None:  # pragma: no cover
            logger.warning("OpenAI no esta disponible, usando fallback heuristico")
            return _fallback_diagnostico(batch_result)

        client = OpenAI(api_key=openai_key)
        summary = _build_summary(batch_result)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": summary},
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content
        if not content or not content.strip():
            logger.warning("OpenAI devolvio contenido vacio, usando fallback heuristico")
            return _fallback_diagnostico(batch_result)
        return content.strip()
    except Exception as e:  # noqa: BLE001 - nunca propagar al caller
        logger.warning("Falla al llamar OpenAI para diagnostico (%s); usando fallback", e)
        return _fallback_diagnostico(batch_result)


__all__ = ["analizar_diagnostico"]
