from __future__ import annotations

from juez.evaluation.judge_engine import JudgeEngine
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, LLMConfig


def test_llm_average_runs_retries(monkeypatch):
    spec = EvaluationSpec(
        run_id="t",
        metrics=[MetricSpec(name="answer_relevancy", threshold=0.7, enabled=True)],
        llm_config=LLMConfig(retries=1, timeout_s=1, average_runs=2, fail_on_variance=False),
    )
    engine = JudgeEngine(spec)
    calls = {"n": 0}

    def fake_once(payload, name, threshold, timeout_s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timeout")
        return engine._build_metric_from_worker_result(
            name, threshold, {"score": 0.9, "reason": "ok", "success": True}
        )

    monkeypatch.setattr(engine, "_eval_llm_metric_once", fake_once)
    res = engine._eval_llm_metric_with_timeout({"metric_name": "answer_relevancy"}, "answer_relevancy", 0.7)
    assert res.retries_used >= 1
    assert res.score is not None
    assert res.infra_error is False

    calls["n"] = 0

    def fake_once_two(payload, name, threshold, timeout_s):
        calls["n"] += 1
        score = 0.8 if calls["n"] == 1 else 0.6
        return engine._build_metric_from_worker_result(
            name, threshold, {"score": score, "reason": "ok", "success": True}
        )

    monkeypatch.setattr(engine, "_eval_llm_metric_once", fake_once_two)
    res2 = engine._eval_llm_metric_with_timeout({"metric_name": "answer_relevancy"}, "answer_relevancy", 0.7)
    assert res2.std_dev is not None
    assert res2.samples is not None
