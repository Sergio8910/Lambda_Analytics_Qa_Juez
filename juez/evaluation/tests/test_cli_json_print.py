from __future__ import annotations

import json

from juez.evaluation.report_models import (
    CaseFeedback,
    CaseReport,
    EvaluationSpec,
    MetricResult,
    RunReport,
    RunSummary,
)
from juez.evaluation.utils_json import render_case_json


def test_print_case_json_redacts() -> None:
    spec = EvaluationSpec(run_id="t", metrics=[])
    metric = MetricResult(
        name="answer_relevancy",
        score=1.0,
        threshold=0.8,
        success=True,
        reason="OK",
        raw={"token": "sk-proj-1234567890"},
    )
    case = CaseReport(
        case_id="C1",
        tags=["demo"],
        severity="baja",
        passed=True,
        metrics=[metric],
        feedback=CaseFeedback(),
    )
    summary = RunSummary(
        run_id="t",
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        pass_rate=1.0,
        by_metric_failures={},
        by_tag_failures={},
        recommendations=[],
    )
    report = RunReport(summary=summary, cases=[case], spec=spec)
    dumped = render_case_json(report, "C1", indent=2, redact=True)
    data = json.loads(dumped)
    assert data["case_id"] == "C1"
    assert "metrics" in data
    assert "feedback" in data
    assert "sk-proj-" not in dumped
