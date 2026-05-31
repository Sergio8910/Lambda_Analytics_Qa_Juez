from __future__ import annotations

from pathlib import Path

from juez.evaluation.report_models import EvaluationSpec, RunSummary, RunReport, CaseReport
import pytest

from juez.evaluation.reporting.forensic import build_forensic_report, render_forensic_pdf


def test_forensic_report_and_pdf() -> None:
    spec = EvaluationSpec(run_id="forensic-test", metrics=[])
    summary = RunSummary(
        run_id="forensic-test",
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        pass_rate=1.0,
        by_metric_failures={},
        by_tag_failures={},
        recommendations=[],
        reliability_score=1.0,
    )
    report = RunReport(
        summary=summary,
        cases=[CaseReport(case_id="C1", tags=[], severity="baja", passed=True, metrics=[])],
        spec=spec,
    )
    forensic = build_forensic_report(report, spec, spec.audit_mode, "spec.json")
    assert forensic["meta"]["run_id"] == "forensic-test"
    assert "production_ready" in forensic["risk_assessment"]

    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / "forensic_test.pdf"
    try:
        import reportlab  # noqa: F401
    except ImportError:
        pytest.skip("reportlab not installed")
    render_forensic_pdf(forensic, str(out_pdf))
    assert out_pdf.exists()
    assert out_pdf.stat().st_size > 1000
