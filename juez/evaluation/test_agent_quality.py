from __future__ import annotations

from juez.evaluation.core.engine import EvaluationEngine
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, TestCase
from juez.evaluation.contracts import RunnerResult


def test_agent_quality() -> None:
    spec = EvaluationSpec(
        run_id="test-agent-quality",
        agent_module="agent",
        agent_function="run_agent",
        enable_metamorphic=False,
        metrics=[
            MetricSpec(name="task_success_deterministic", threshold=0.66, enabled=True, config={}),
            MetricSpec(
                name="unsupported_claims",
                threshold=0.50,
                enabled=True,
                config={"ignore_unverifiable": True, "penalize_numbers": True},
            ),
            MetricSpec(name="format_compliance", threshold=1.0, enabled=True, config={}),
        ],
    )
    case = TestCase(case_id="T1", input="Hola", tags=[], severity="baja", expected_behavior="")
    engine = EvaluationEngine(spec)
    def _runner(_: TestCase) -> RunnerResult:
        return RunnerResult(output_text="Hola", retrieval_context=[], latency_ms=1.0)

    report = engine.evaluate_run([case], _runner)
    assert report.summary.reliability_score is not None
    assert report.summary.reliability_score >= 0.90
    gating = spec.gating_metrics or [
        "task_success_deterministic",
        "unsupported_claims",
        "format_compliance",
    ]
    enabled = {m.name for m in spec.metrics if m.enabled}
    gating = [g for g in gating if g in enabled]
    case_report = report.cases[0]
    for m in case_report.metrics:
        if m.name in gating:
            assert m.success is not False
