from evaluation.judge_engine import JudgeEngine
from evaluation.report_models import EvaluationSpec


def test_task_success_deterministic_empty_case() -> None:
    spec = EvaluationSpec(run_id="t", metrics=[])
    engine = JudgeEngine(spec)
    res = engine._metric_task_success_deterministic(
        user_input="Hola",
        tags=[],
        output="Hola",
        threshold=0.67,
        context=[],
        expected_behavior="",
        expected_output="",
    )
    assert res.success is True
    assert res.score == 1.0
    assert res.reason == "Sin criterios determinísticos aplicables."
