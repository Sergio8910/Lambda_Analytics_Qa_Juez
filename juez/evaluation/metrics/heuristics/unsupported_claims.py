from __future__ import annotations

from ...core.models import NormalizedRun
from ...judge_engine import extract_claims, score_claims_against_context
from ...report_models import MetricResult


def evaluate(normalized: NormalizedRun, threshold: float, ignore_unverifiable: bool = False, penalize_numbers: bool = True) -> MetricResult:
    context = normalized.context.retrieval_context or normalized.context.provided_context
    claims = extract_claims(normalized.execution.output_text)
    analysis = score_claims_against_context(claims, context, penalize_numbers=penalize_numbers)
    supported = sum(1 for c in analysis.claims if c.verdict == "supported")
    contradicted = sum(1 for c in analysis.claims if c.verdict == "contradicted")
    if ignore_unverifiable:
        denom = supported + contradicted
        score = (supported / denom) if denom > 0 else 1.0
        reason = "Proporción de afirmaciones soportadas (ignorando unverifiable)."
    else:
        total = max(len(analysis.claims), 1)
        score = supported / total
        reason = "Proporción de afirmaciones soportadas por el contexto."
    return MetricResult(
        name="unsupported_claims",
        score=score,
        threshold=threshold,
        success=score >= threshold,
        reason=reason,
        reason_es=reason,
        raw={"claims": [c.model_dump() for c in analysis.claims]},
    )
