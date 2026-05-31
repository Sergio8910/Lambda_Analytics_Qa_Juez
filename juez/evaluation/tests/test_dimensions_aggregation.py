from __future__ import annotations

from juez.evaluation.scorecard.dimensions import build_dimensions
from juez.evaluation.report_models import MetricResult


def test_dimensions_with_skipped():
    metrics = [
        MetricResult(name="task_success_deterministic", score=0.8, success=True),
        MetricResult(name="answer_relevancy", score=0.6, success=True),
        MetricResult(name="faithfulness", score=None, skipped=True),
        MetricResult(name="contextual_precision", score=0.9, success=True),
        MetricResult(name="hallucination", score=1.0, success=True),
        MetricResult(name="unsupported_claims", score=0.7, success=True),
        MetricResult(name="latency_budget", score=1.0, success=True),
    ]
    dims = build_dimensions(metrics, has_context=True)
    assert dims["correctness"].score is not None
    assert dims["grounding"].score is not None
    assert any("SKIPPED" in n for n in dims["grounding"].notes)
