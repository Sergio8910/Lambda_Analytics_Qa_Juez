from __future__ import annotations

from juez.evaluation.api_schema import build_api_report
from juez.evaluation.report_models import (
    CaseReport,
    EvaluationSpec,
    MetricResult,
    RunReport,
    RunSummary,
)


def test_api_output_schema():
    spec = EvaluationSpec(run_id="t", metrics=[])
    summary = RunSummary(run_id="t", total_cases=1, passed_cases=1, failed_cases=0, pass_rate=1.0)
    case = CaseReport(
        case_id="C1",
        tags=[],
        severity="baja",
        passed=True,
        metrics=[MetricResult(name="task_success_deterministic", score=1.0, success=True)],
        dimensions={"correctness": {"score": 1.0, "evidence": [], "notes": []}},
        scorecard={"overall_score": 1.0, "weights": {}, "eligible_dimensions": [], "scorecard_passed": True, "gates": {}, "notes": []},
        anti_gaming={"flags": [], "penalty": None, "notes": []},
    )
    report = RunReport(summary=summary, cases=[case], spec=spec)
    api = build_api_report(report)
    assert "run_id" in api
    assert "cases" in api
    assert api["cases"][0]["case_id"] == "C1"
