from __future__ import annotations

from juez.evaluation.report_models import (
    CaseReport,
    EvaluationSpec,
    MetricResult,
    RunReport,
    RunSummary,
)


def test_report_schema_keys() -> None:
    spec = EvaluationSpec(run_id="test-run", metrics=[])
    case = CaseReport(case_id="C1", tags=[], severity="baja", passed=True, metrics=[])
    summary = RunSummary(
        run_id="test-run",
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        pass_rate=1.0,
        by_metric_failures={},
        by_tag_failures={},
        recommendations=[],
    )
    report = RunReport(summary=summary, cases=[case], spec=spec)
    data = report.model_dump()
    assert set(data.keys()) == {"summary", "cases", "spec"}
    assert "run_id" in data["summary"]
