from __future__ import annotations

import json
from typing import Optional

from ...core.models import NormalizedRun
from ...report_models import MetricResult


def evaluate(normalized: NormalizedRun, threshold: float) -> MetricResult:
    if normalized.contract.output_format != "json":
        return MetricResult(
            name="format_compliance",
            score=None,
            threshold=threshold,
            success=None,
            reason="Métrica omitida: output_format no es JSON.",
            reason_es="Métrica omitida: output_format no es JSON.",
            skipped=True,
            skip_reason="no_json",
            raw={"skipped": True, "output_format": normalized.contract.output_format},
        )
    try:
        parsed = json.loads(normalized.execution.output_text)
    except Exception as exc:
        return MetricResult(
            name="format_compliance",
            score=0.0,
            threshold=threshold,
            success=False,
            reason="La salida no es JSON válido.",
            reason_es="La salida no es JSON válido.",
            error=str(exc),
            raw={},
        )
    return MetricResult(
        name="format_compliance",
        score=1.0,
        threshold=threshold,
        success=True,
        reason="JSON válido y cumple requisitos básicos.",
        reason_es="JSON válido y cumple requisitos básicos.",
        raw={"parsed": parsed},
    )
