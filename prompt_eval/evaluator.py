"""Orquestador del evaluador de prompts.

Combina reglas estáticas + LLM judge en un único resultado con score global,
scores por dimensión, hallazgos y recomendaciones priorizadas.

Filosofía de scoring (alineada con Juez v2):
  - Cada dimensión empieza en 100.
  - Cada finding descuenta `SEVERITY_PENALTY[severity]` puntos a la
    dimensión a la que pertenece, con clipping en 0.
  - Score global = promedio ponderado de las dimensiones con
    DIMENSION_WEIGHTS.
  - Veredicto se deriva del score global con cortes fijos.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from .llm_judge import run_llm_judge
from .models import (
    DIMENSION_WEIGHTS,
    SEVERITY_PENALTY,
    Dimension,
    DimensionScore,
    Finding,
    PromptEvalRequest,
    PromptEvalResult,
    PromptMetrics,
    Severity,
)
from .rules import (
    _detect_idioma_simple,
    _variantes_tool_para_busqueda,
    run_all_rules,
)


# =============================================================================
# Métricas y helpers
# =============================================================================


_HEADER_RE = re.compile(r"^\s*(?:#{1,4}\s+|\[)\s*([^\n\]]{2,80})\s*\]?\s*$", re.MULTILINE)
_PLACEHOLDER_RE = re.compile(r"\{\{?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}?\}")


def _build_metricas(prompt: str, ctx: Dict[str, Any]) -> PromptMetrics:
    palabras = re.findall(r"\S+", prompt)
    lineas = prompt.splitlines()
    secciones = [m.strip() for m in _HEADER_RE.findall(prompt)][:20]
    placeholders = sorted(set(_PLACEHOLDER_RE.findall(prompt)))
    tools_mencionadas: List[str] = []
    if ctx.get("tools"):
        prompt_lower = prompt.lower()
        for nombre in ctx["tools"]:
            variantes = _variantes_tool_para_busqueda(nombre)
            if any(v.lower() in prompt_lower for v in variantes):
                tools_mencionadas.append(nombre)
    return PromptMetrics(
        longitud_chars=len(prompt),
        longitud_palabras=len(palabras),
        longitud_lineas=len(lineas),
        longitud_estimada_tokens=max(1, len(prompt) // 4),
        idioma_detectado=_detect_idioma_simple(prompt),
        secciones_detectadas=secciones,
        placeholders_detectados=placeholders,
        menciona_tools=tools_mencionadas,
    )


def _score_por_dimension(findings: List[Finding]) -> List[DimensionScore]:
    """Calcula score por cada dimensión empezando en 100 y restando penalties."""
    penalty_por_dim: Dict[Dimension, int] = defaultdict(int)
    findings_por_dim: Dict[Dimension, List[Finding]] = defaultdict(list)
    for f in findings:
        penalty_por_dim[f.dimension] += SEVERITY_PENALTY[f.severity]
        findings_por_dim[f.dimension].append(f)

    resultados: List[DimensionScore] = []
    for dim, weight in DIMENSION_WEIGHTS.items():
        score = max(0.0, 100.0 - penalty_por_dim[dim])
        sev_count = Counter(f.severity.value for f in findings_por_dim[dim])
        resultados.append(
            DimensionScore(
                dimension=dim,
                score=round(score, 1),
                weight=weight,
                findings_count=len(findings_por_dim[dim]),
                findings_by_severity=dict(sev_count),
            )
        )
    return resultados


def _score_global(dimensiones: List[DimensionScore]) -> float:
    total_weight = sum(d.weight for d in dimensiones) or 1.0
    weighted = sum(d.score * d.weight for d in dimensiones) / total_weight
    return round(weighted, 1)


def _veredicto(score: float) -> str:
    if score >= 90:
        return "excelente"
    if score >= 75:
        return "bueno"
    if score >= 60:
        return "aceptable"
    if score >= 40:
        return "deficiente"
    return "critico"


def _top_recomendaciones(findings: List[Finding], n: int = 5) -> List[str]:
    """Ordena findings por impacto (severity * peso de dimensión) y devuelve recomendaciones."""
    def impacto(f: Finding) -> float:
        return SEVERITY_PENALTY[f.severity] * DIMENSION_WEIGHTS.get(f.dimension, 0.1)

    ordenados = sorted(
        [f for f in findings if f.recomendacion],
        key=impacto,
        reverse=True,
    )
    recos: List[str] = []
    seen: set = set()
    for f in ordenados:
        reco = f.recomendacion or ""
        clave = reco[:80]
        if clave in seen:
            continue
        seen.add(clave)
        recos.append(f"[{f.rule_id} · {f.severity.value}] {reco}")
        if len(recos) >= n:
            break
    return recos


# =============================================================================
# Orquestador principal
# =============================================================================


def evaluate_prompt(request: PromptEvalRequest) -> PromptEvalResult:
    """Evalúa un prompt y retorna un resultado completo.

    No lanza excepciones de negocio — los problemas se reportan como
    findings con severidad, no rompiendo la respuesta.
    """
    t0 = time.monotonic()
    prompt = request.prompt
    ctx: Dict[str, Any] = {
        "nombre": request.nombre,
        "expected_language": request.expected_language,
        "expected_output_format": request.expected_output_format,
        "tools": request.tools,
        "domain": request.domain,
    }

    metricas = _build_metricas(prompt, ctx)

    # 1. Reglas determinísticas (siempre)
    findings = run_all_rules(prompt, ctx)

    # 2. LLM judge (opcional)
    llm_meta: Dict[str, Any] = {"skipped": True, "reason": "no_solicitado"}
    if request.incluir_llm_judge:
        llm_findings, llm_meta = run_llm_judge(prompt, ctx, model=request.llm_model)
        findings.extend(llm_findings)

    # 3. Scoring
    dimensiones = _score_por_dimension(findings)
    score_global = _score_global(dimensiones)

    # 4. Resumen y top recomendaciones
    sev_counter = Counter(f.severity.value for f in findings)
    duracion_ms = int((time.monotonic() - t0) * 1000)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]

    return PromptEvalResult(
        nombre=request.nombre,
        score_global=score_global,
        veredicto=_veredicto(score_global),
        dimensiones=dimensiones,
        findings=findings,
        findings_resumen=dict(sev_counter),
        metricas=metricas,
        top_recomendaciones=_top_recomendaciones(findings),
        llm_judge_aplicado=not llm_meta.get("skipped", True),
        duracion_ms=duracion_ms,
        meta={
            "prompt_hash": prompt_hash,
            "llm": llm_meta,
            "reglas_evaluadas": len(findings),
        },
    )
