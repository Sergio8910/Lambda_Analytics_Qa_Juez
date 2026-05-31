from __future__ import annotations

from ...core.models import NormalizedRun
from ...report_models import MetricResult


def evaluate(normalized: NormalizedRun, threshold: float, budget_ms: float, jitter_ms: float = 250.0) -> MetricResult:
    latency_ms = normalized.execution.trace.latency_ms
    success = latency_ms <= (budget_ms + jitter_ms)
    score = 1.0 if success else 0.0
    return MetricResult(
        name="latency_budget",
        score=score,
        threshold=threshold,
        success=success,
        reason=f"Latencia {latency_ms:.2f} ms (budget {budget_ms} ms, jitter {jitter_ms} ms).",
        reason_es=f"Latencia {latency_ms:.2f} ms (budget {budget_ms} ms, jitter {jitter_ms} ms).",
        raw={"budget_ms": budget_ms, "jitter_ms": jitter_ms, "latency_ms": latency_ms},
    )
