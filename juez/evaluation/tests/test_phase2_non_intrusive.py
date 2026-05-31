from __future__ import annotations

from juez.evaluation.report_models import EvaluationSpec, MetricSpec, TestCase
from juez.evaluation.core.engine import EvaluationEngine
from juez.evaluation.contracts import RunnerResult


def test_phase2_non_intrusive():
    spec = EvaluationSpec(
        run_id="t",
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
            MetricSpec(name="latency_budget", threshold=1.0, enabled=True, config={"jitter_ms": 300}),
        ],
    )
    case = TestCase(case_id="T1", input="Hola", tags=[], severity="baja", expected_behavior="")
    engine = EvaluationEngine(spec)
    def _runner(_: TestCase) -> RunnerResult:
        return RunnerResult(output_text="Hola", retrieval_context=[], latency_ms=1.0)

    report = engine.evaluate_run([case], _runner)
    assert report.summary.failed_cases == 0
    assert report.summary.passed_cases == 1
