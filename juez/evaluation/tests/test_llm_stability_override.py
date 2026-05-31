from __future__ import annotations

from juez.evaluation.judge_engine import JudgeEngine
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, LLMConfig


def test_llm_timeout_override(monkeypatch):
    spec = EvaluationSpec(
        run_id="t",
        metrics=[MetricSpec(name="answer_relevancy", threshold=0.7, enabled=True, config={"timeout_s": 60})],
        llm_config=LLMConfig(retries=0, timeout_s=10, average_runs=1, fail_on_variance=False),
    )
    engine = JudgeEngine(spec)

    def fake_once(payload, name, threshold, timeout_s):
        if timeout_s < 60:
            raise TimeoutError("timeout")
        return engine._build_metric_from_worker_result(
            name, threshold, {"score": 0.85, "reason": "ok", "success": True}
        )

    monkeypatch.setattr(engine, "_eval_llm_metric_once", fake_once)
    res = engine._eval_llm_metric_with_timeout(
        {"metric_name": "answer_relevancy"},
        "answer_relevancy",
        0.7,
        {"timeout_s": 60},
    )
    assert res.skipped is False
    assert res.infra_error is False
    assert res.score is not None
    assert res.raw.get("effective_timeout_s") == 60
