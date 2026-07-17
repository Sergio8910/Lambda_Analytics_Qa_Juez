"""Orquestador de certificación de proyectos de La Colmena.

Une el ciclo completo en un solo lazo con convergencia:

    analizar -> evaluar -> construir (self-heal) -> re-evaluar -> iterar ...
    ... hasta CONVERGER (una ronda ya no mejora nada) -> CERTIFICAR

No duplica lógica: reusa `evaluate_project_path` (análisis multi-dimensión +
cobertura) para analizar/evaluar y `run_self_heal` (propone/aplica/re-evalúa/
rollback) para construir. Lo que agrega es el nivel-proyecto que faltaba: correr
hasta que ya no haya más que arreglar y emitir un CERTIFICADO honesto —
consciente de la cobertura (solo certifica lo que realmente se evaluó).

Opera SIEMPRE sobre un project_path (una carpeta). El wrapper de runner arma un
proyecto temporal efímero, así que el self-heal escribe ahí y nunca toca un
repo/infraestructura real.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .project_evaluator import evaluate_project_path


def _snapshot(report) -> Dict[str, Any]:
    """Foto compacta de una ronda de evaluación (sin arrastrar todo el reporte)."""
    s = report.score
    return {
        "score": s.score,
        "estado": s.status,
        "criticos": s.critical_findings,
        "altos": s.high_findings,
        "medios": s.medium_findings,
        "total_hallazgos": len(report.findings),
    }


def _findings_serializables(report) -> List[Dict[str, Any]]:
    return [
        {
            "id": f.id, "severidad": f.severity, "categoria": f.category,
            "titulo": f.title, "archivo": f.file, "recomendacion": f.recommendation,
        }
        for f in report.findings
    ]


def _veredicto(report) -> Dict[str, Any]:
    """Regla de certificación, CONSCIENTE DE COBERTURA: no se puede certificar
    'todo bien' sobre dimensiones que no se evaluaron."""
    s = report.score
    cobertura = getattr(report, "coverage", {}) or {}
    cobertura_completa = bool(cobertura.get("completa"))

    if s.critical_findings > 0:
        return {"certificado": False, "veredicto": "NO_CERTIFICADO",
                "motivo": f"Hay {s.critical_findings} hallazgo(s) crítico(s) sin resolver."}
    if s.high_findings > 0:
        return {"certificado": False, "veredicto": "NO_CERTIFICADO",
                "motivo": f"Hay {s.high_findings} hallazgo(s) de severidad alta sin resolver."}
    if s.score >= 85.0 and cobertura_completa:
        return {"certificado": True, "veredicto": "CERTIFICADO",
                "motivo": "Score alto, sin críticos/altos y cobertura completa."}
    if s.score >= 70.0:
        motivo = "Sin críticos/altos, pero " + (
            "la cobertura no es completa (hay dimensiones sin evaluar)."
            if not cobertura_completa else "el score no llega al umbral de certificación plena (85)."
        )
        return {"certificado": True, "veredicto": "CERTIFICADO_CON_OBSERVACIONES", "motivo": motivo}
    return {"certificado": False, "veredicto": "NO_CERTIFICADO",
            "motivo": f"Score {s.score} por debajo del mínimo aceptable."}


def _tokens_de(summary: Any) -> tuple[int, float]:
    """(tokens, usd) de un cost summary (dynamic_cost_summary / generic_fixer),
    tolerante a None/claves faltantes."""
    if not isinstance(summary, dict):
        return 0, 0.0
    return int(summary.get("total_tokens") or 0), float(summary.get("total_cost_usd") or 0.0)


def certificar_proyecto(
    project_path: Path | str,
    *,
    max_rondas: int = 4,
    incluir_dinamicas: bool = False,
    auto_fix: bool = True,
    min_confidence: float = 0.85,
    max_lines_per_fix: int = 40,
    enable_generic_fixer: bool = False,
    presupuesto_tokens: int | None = None,
    presupuesto_usd: float | None = None,
    output_dir: Path | str = "outputs",
) -> Dict[str, Any]:
    """Corre el ciclo analizar->evaluar->construir->iterar hasta converger y
    emite un certificado. Devuelve un dict serializable.

    Convergencia = una ronda de self-heal ya no aplica NINGÚN fix (kept==0), o
    ya no quedan críticos/altos, o se agotan las rondas, o se agota el
    presupuesto de tokens/USD. La parada es explícita (motivo_parada).

    Optimización de tokens (importante): el análisis estático es Python
    determinista y cuesta CERO tokens, así que el LAZO de fixes corre solo con
    evaluación estática (gratis). La capa dinámica cara (LLM: adversarial/edge/
    propósito) corre UNA sola vez al final, para el certificado -- no en cada
    ronda. Cada llamada LLM ya usa contexto reducido (un caso, no el proyecto
    entero), así que el "enjambre de contexto" es implícito por diseño.
    """
    root = Path(project_path).resolve()
    tokens_gastados = 0
    usd_gastado = 0.0

    def presupuesto_excedido() -> bool:
        if presupuesto_tokens is not None and tokens_gastados >= presupuesto_tokens:
            return True
        if presupuesto_usd is not None and usd_gastado >= presupuesto_usd:
            return True
        return False

    # Evaluación inicial ESTÁTICA (gratis) para conducir el lazo de fixes.
    report = evaluate_project_path(root, incluir_dinamicas=False)
    inicial = _snapshot(report)
    rondas: List[Dict[str, Any]] = [{"ronda": 0, "fase": "evaluacion_inicial_estatica", **inicial}]

    motivo_parada = "sin_auto_fix"
    convergio = False

    if auto_fix:
        from .self_heal_agent import run_self_heal

        for i in range(1, max_rondas + 1):
            if report.score.critical_findings == 0 and report.score.high_findings == 0:
                motivo_parada = "sin_criticos_ni_altos"
                convergio = True
                break
            if presupuesto_excedido():
                motivo_parada = "cortado_por_presupuesto"
                break

            heal = run_self_heal(
                root,
                min_confidence=min_confidence,
                max_iterations=3,
                max_lines_per_fix=max_lines_per_fix,
                output_dir=output_dir,
                enable_generic_fixer=enable_generic_fixer,
            )
            t, u = _tokens_de(getattr(heal, "generic_fixer_cost_summary", None))
            tokens_gastados += t
            usd_gastado += u
            report = evaluate_project_path(root, incluir_dinamicas=False)  # estático: gratis
            rondas.append({
                "ronda": i, "fase": "construccion_y_reevaluacion_estatica",
                "fixes_aplicados": heal.kept_fixes,
                "fixes_revertidos": heal.rolled_back_fixes,
                "bloqueados": heal.blocked_findings,
                **_snapshot(report),
            })

            if heal.kept_fixes == 0:
                motivo_parada = "sin_mejoras_en_la_ronda"
                convergio = True
                break
        else:
            motivo_parada = "max_rondas_alcanzado"

    # Evaluación final: si se pidieron dinámicas Y hay presupuesto, se corre UNA
    # vez la capa cara para que el certificado refleje también lo dinámico.
    dinamicas_ejecutadas = False
    if incluir_dinamicas and not presupuesto_excedido():
        report = evaluate_project_path(root, incluir_dinamicas=True)
        dinamicas_ejecutadas = True
        t, u = _tokens_de(getattr(report, "dynamic_cost_summary", None))
        tokens_gastados += t
        usd_gastado += u
        rondas.append({"ronda": len(rondas), "fase": "evaluacion_final_dinamica", **_snapshot(report)})

    certificado = _veredicto(report)
    return {
        "kind": "certificacion",
        **certificado,
        "convergio": convergio,
        "motivo_parada": motivo_parada,
        "score_inicial": inicial["score"],
        "score_final": report.score.score,
        "estado_final": report.score.status,
        "rondas": rondas,
        "dinamicas_ejecutadas": dinamicas_ejecutadas,
        "presupuesto": {
            "tokens_gastados": tokens_gastados,
            "usd_gastado": round(usd_gastado, 4),
            "tope_tokens": presupuesto_tokens,
            "tope_usd": presupuesto_usd,
            "cortado_por_presupuesto": motivo_parada == "cortado_por_presupuesto",
        },
        "hallazgos_restantes": _findings_serializables(report),
        "requiere_revision_humana": [
            {"id": f.id, "severidad": f.severity, "categoria": f.category, "titulo": f.title}
            for f in report.findings if f.severity in {"critical", "high"}
        ],
        "cobertura": getattr(report, "coverage", {}) or {},
        "nota": (
            "Certificación consciente de cobertura: solo se certifica 'todo bien' sobre las "
            "dimensiones realmente evaluadas (ver 'cobertura'). El lazo de fixes corre con "
            "análisis estático (gratis); la capa dinámica LLM corre una vez al final. "
            f"El ciclo se detuvo por: {motivo_parada}."
        ),
    }
