from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..report_models import MetricResult


@dataclass
class DimensionEvidence:
    name: str
    score: Optional[float]
    success: Optional[bool]
    std_dev: Optional[float] = None


@dataclass
class DimensionResult:
    score: Optional[float]
    evidence: List[DimensionEvidence]
    notes: List[str]


def _metric_by_name(metrics: List[MetricResult]) -> Dict[str, MetricResult]:
    return {m.name: m for m in metrics}


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / max(len(values), 1)


def build_dimensions(
    metrics: List[MetricResult],
    has_context: bool,
) -> Dict[str, DimensionResult]:
    by_name = _metric_by_name(metrics)
    dims: Dict[str, DimensionResult] = {}

    # correctness
    evid: List[DimensionEvidence] = []
    notes: List[str] = []
    correctness_scores: List[float] = []
    if "task_success" in by_name and by_name["task_success"].score is not None:
        m = by_name["task_success"]
        correctness_scores.append(m.score)
        evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
    elif "task_success_deterministic" in by_name and by_name["task_success_deterministic"].score is not None:
        m = by_name["task_success_deterministic"]
        correctness_scores.append(m.score)
        evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
    else:
        notes.append("Sin task_success disponible; se omite.")
    if "answer_relevancy" in by_name and by_name["answer_relevancy"].score is not None:
        m = by_name["answer_relevancy"]
        correctness_scores.append(m.score)
        evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
    score = _mean(correctness_scores)
    dims["correctness"] = DimensionResult(score=score, evidence=evid, notes=notes)

    # grounding
    evid = []
    notes = []
    grounding_scores: List[float] = []
    for name in ["faithfulness", "contextual_precision", "hallucination"]:
        m = by_name.get(name)
        if not m:
            continue
        if m.skipped:
            notes.append(f"{name} SKIPPED, no penaliza.")
            continue
        if m.score is not None:
            grounding_scores.append(m.score)
            evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
    if not has_context:
        notes.append("Sin retrieval_context; grounding no aplica.")
    score = _mean(grounding_scores)
    dims["grounding"] = DimensionResult(score=score, evidence=evid, notes=notes)

    # instruction_following
    evid = []
    notes = []
    m = by_name.get("instruction_adherence")
    if m and not m.skipped:
        evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
        score = m.score
    else:
        score = None
        notes.append("Sin instruction_adherence disponible.")
    dims["instruction_following"] = DimensionResult(score=score, evidence=evid, notes=notes)

    # safety_integrity
    evid = []
    notes = []
    m = by_name.get("unsupported_claims")
    score = m.score if m and not m.skipped else None
    if m:
        evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
    else:
        notes.append("Sin unsupported_claims disponible.")
    dims["safety_integrity"] = DimensionResult(score=score, evidence=evid, notes=notes)

    # performance
    evid = []
    notes = []
    m = by_name.get("latency_budget")
    score = m.score if m and not m.skipped else None
    if m:
        evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
    else:
        notes.append("Sin latency_budget disponible.")
    dims["performance"] = DimensionResult(score=score, evidence=evid, notes=notes)

    # reliability
    evid = []
    notes = []
    rel_scores: List[float] = []
    infra_errors = False
    model_errors = False
    for m in metrics:
        if m.std_dev is not None:
            evid.append(DimensionEvidence(m.name, m.score, m.success, m.std_dev))
            rel_scores.append(max(0.0, 1.0 - m.std_dev))
        if m.infra_error:
            infra_errors = True
        if m.model_error:
            model_errors = True
    if infra_errors:
        notes.append("Se detectaron errores de infraestructura LLM.")
        rel_scores.append(0.5)
    if model_errors:
        notes.append("Se detectaron errores de modelo.")
        rel_scores.append(0.5)
    score = _mean(rel_scores)
    dims["reliability"] = DimensionResult(score=score, evidence=evid, notes=notes)

    return dims
