from __future__ import annotations

from evaluation.report_models import EvaluationSpec, MetricSpec, MetricResult, CaseReport
from evaluation.core.engine_impl import JudgeEngine


def _build_spec(metrics, gating=None):
    return EvaluationSpec(
        run_id="test-enterprise",
        metrics=metrics,
        grading_mode="rubric",
        gating_metrics=gating or [],
        audit_mode="enterprise",
        scorecard_min_pass_rate=0.80,
        reliability_min=0.90,
    )


def _case(case_id: str, passed: bool, severity: str, metrics):
    return CaseReport(
        case_id=case_id,
        tags=[],
        severity=severity,
        passed=passed,
        metrics=metrics,
    )


def test_enterprise_gating_fail():
    spec = _build_spec(
        metrics=[
            MetricSpec(name="unsupported_claims", threshold=0.7, enabled=True),
        ],
        gating=["unsupported_claims"],
    )
    engine = JudgeEngine(spec)
    case = _case(
        "C1",
        passed=False,
        severity="media",
        metrics=[
            MetricResult(
                name="unsupported_claims",
                score=0.0,
                threshold=0.7,
                success=False,
                reason="fallo",
            )
        ],
    )
    summary = engine._build_summary([case])
    exec_sum = summary.executive_summary
    assert exec_sum is not None
    assert exec_sum["scorecard_passed"] is False
    assert exec_sum["verdict"] == "NO CUMPLE"


def test_enterprise_reliability_riesgo_operacional():
    spec = _build_spec(
        metrics=[
            MetricSpec(name="task_success_deterministic", threshold=0.7, enabled=True),
        ],
        gating=["task_success_deterministic"],
    )
    engine = JudgeEngine(spec)
    case = _case(
        "C1",
        passed=True,
        severity="media",
        metrics=[
            MetricResult(
                name="task_success_deterministic",
                score=1.0,
                threshold=0.7,
                success=True,
                infra_skipped=True,
            )
        ],
    )
    summary = engine._build_summary([case])
    exec_sum = summary.executive_summary
    assert exec_sum is not None
    assert exec_sum["reliability_score"] is not None
    assert exec_sum["reliability_score"] < spec.reliability_min
    assert exec_sum["verdict"] == "RIESGO OPERACIONAL"


def test_enterprise_severity_blocker():
    spec = _build_spec(
        metrics=[MetricSpec(name="format_compliance", threshold=1.0, enabled=True)],
        gating=["format_compliance"],
    )
    engine = JudgeEngine(spec)
    case = _case(
        "C1",
        passed=False,
        severity="alta",
        metrics=[
            MetricResult(
                name="format_compliance",
                score=0.0,
                threshold=1.0,
                success=False,
            )
        ],
    )
    summary = engine._build_summary([case])
    exec_sum = summary.executive_summary
    assert exec_sum is not None
    assert "C1" in exec_sum["severity_blockers"]
    assert exec_sum["verdict"] == "NO CUMPLE"


def test_enterprise_risk_score_range():
    spec = _build_spec(
        metrics=[MetricSpec(name="format_compliance", threshold=1.0, enabled=True)],
        gating=["format_compliance"],
    )
    engine = JudgeEngine(spec)
    case = _case(
        "C1",
        passed=False,
        severity="media",
        metrics=[
            MetricResult(
                name="format_compliance",
                score=0.0,
                threshold=1.0,
                success=False,
            )
        ],
    )
    summary = engine._build_summary([case])
    exec_sum = summary.executive_summary
    assert exec_sum is not None
    assert 0.0 <= exec_sum["risk_score"] <= 1.0
    assert exec_sum["risk_level"] in {"BAJO", "MEDIO", "ALTO"}
