from __future__ import annotations

from fastapi.testclient import TestClient

from evaluation.api import server
from evaluation.report_models import CaseReport, EvaluationSpec, RunReport, RunSummary


def test_api_health_and_auto_evaluate(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "test-key")
    client = TestClient(server.app)

    def _fake_run_engine(spec, cases):
        summary = RunSummary(
            run_id=spec.run_id,
            total_cases=len(cases),
            passed_cases=len(cases),
            failed_cases=0,
            pass_rate=1.0,
        )
        summary.executive_summary = {"verdict": "CUMPLE"}
        case = CaseReport(
            case_id=cases[0].case_id if cases else "C1",
            tags=[],
            severity="baja",
            passed=True,
            metrics=[],
        )
        return RunReport(summary=summary, cases=[case], spec=spec)

    monkeypatch.setattr(server, "run_engine", _fake_run_engine)

    resp = client.get("/health")
    assert resp.status_code == 200

    payload = {
        "run_id": "auto-1",
        "prompt_base": "Asistente de supermercado",
        "n_cases": 3,
        "seed": 7,
    }
    resp = client.post(
        "/v1/auto-evaluate", json=payload, headers={"X-API-KEY": "test-key"}
    )
    assert resp.status_code == 200
    data = resp.json()
    report = data.get("report", {})
    summary = report.get("summary", {})
    assert "executive_summary" in summary
    assert summary["executive_summary"]["verdict"] == "CUMPLE"
