from __future__ import annotations

from ...core.models import NormalizedRun
from ...feedback_generator import build_case_feedback
from ...report_models import MetricResult


def evaluate(normalized: NormalizedRun, threshold: float) -> MetricResult:
    feedback = build_case_feedback(
        user_input=normalized.input.user_message,
        output=normalized.execution.output_text,
        tags=normalized.case.tags,
        metrics=[],
        claim_analysis=None,
        retrieval_context=normalized.context.retrieval_context or normalized.context.provided_context,
    )
    preguntas = feedback.question_by_question
    if not preguntas:
        return MetricResult(
            name="completeness",
            score=None,
            threshold=threshold,
            success=None,
            reason="Sin subpreguntas parseables.",
            reason_es="Sin subpreguntas parseables.",
            skipped=True,
            skip_reason="sin_subpreguntas",
            raw={"status": "skipped", "skip_reason": "sin_subpreguntas"},
        )
    completos = sum(1 for q in preguntas if q.verdict in {"correcto", "parcial"})
    total = len(preguntas)
    score = completos / max(total, 1)
    reason = f"Completas={completos}/{total}"
    return MetricResult(
        name="completeness",
        score=score,
        threshold=threshold,
        success=score >= threshold,
        reason=reason,
        reason_es=reason,
        raw={"total": total, "completas": completos},
    )
