from juez.evaluation.judge_engine import JudgeEngine, _translate_reason
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, TaskContract


def test_skipped_metric_has_none_score_and_success():
    spec = EvaluationSpec(
        run_id="test-skip",
        metrics=[MetricSpec(name="format_compliance", threshold=1.0, enabled=True)],
    )
    engine = JudgeEngine(spec)
    contract = TaskContract(output_format="free_text")
    res = engine._metric_format_compliance(contract, "hola", 1.0)
    assert res.skipped is True
    assert res.score is None
    assert res.success is None


def test_reason_es_no_spanglish_connectors():
    reason = "The score is 0.50 because the response fails to address the question."
    reason_es = _translate_reason(reason)
    assert "razón en inglés (sin traducir)" not in reason_es.lower()
    assert "because" not in reason_es.lower()
    assert "fails to" not in reason_es.lower()
