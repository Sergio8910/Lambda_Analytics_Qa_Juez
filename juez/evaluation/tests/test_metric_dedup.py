from juez.evaluation.judge_engine import JudgeEngine
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, TestCase
from juez.evaluation.runner import RunnerResult


def test_metrics_dedup_by_name() -> None:
    spec = EvaluationSpec(
        run_id="dedup",
        metrics=[
            MetricSpec(name="format_compliance", threshold=1.0, enabled=True),
            MetricSpec(name="format_compliance", threshold=1.0, enabled=True),
            MetricSpec(name="unknown_custom_metric", threshold=1.0, enabled=True),
            MetricSpec(name="unknown_custom_metric", threshold=1.0, enabled=True),
        ],
    )
    engine = JudgeEngine(spec)
    tc = TestCase(case_id="C1", input="Hola", tags=[], severity="baja")
    rr = RunnerResult(output_text="hola", retrieval_context=[], latency_ms=1.0)
    report = engine.evaluate_case(tc, rr)
    names = [m.name for m in report.metrics]
    assert len(names) == len(set(names))
