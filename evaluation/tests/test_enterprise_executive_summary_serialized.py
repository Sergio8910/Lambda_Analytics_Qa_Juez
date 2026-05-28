from __future__ import annotations

from evaluation.report_models import EvaluationSpec, MetricSpec, MetricResult, CaseReport
from evaluation.core.engine_impl import JudgeEngine


def test_enterprise_executive_summary_serialized():
    spec = EvaluationSpec(
        run_id="exec-summary-test",
        metrics=[MetricSpec(name="format_compliance", threshold=1.0, enabled=True)],
        grading_mode="rubric",
        gating_metrics=["format_compliance"],
        audit_mode="enterprise",
    )
    engine = JudgeEngine(spec)
    case = CaseReport(
        case_id="C1",
        tags=[],
        severity="media",
        passed=False,
        metrics=[
            MetricResult(
                name="format_compliance",
                score=0.0,
                threshold=1.0,
                success=False,
                reason="fallo",
            )
        ],
    )
    summary = engine._build_summary([case])
    data = summary.to_dict()
    assert "executive_summary" in data
    assert data["executive_summary"]["audit_mode"] == "enterprise"
