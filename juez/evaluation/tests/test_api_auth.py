from __future__ import annotations

from fastapi.testclient import TestClient

from juez.evaluation.api import server
from juez.evaluation.report_models import CaseReport, RunReport, RunSummary


def _fake_run_engine(spec, cases):
    summary = RunSummary(
        run_id=spec.run_id,
        total_cases=len(cases),
        passed_cases=len(cases),
        failed_cases=0,
        pass_rate=1.0,
        reliability_score=1.0,
    )
    summary.executive_summary = {"verdict": "CUMPLE"}
    case = CaseReport(
        case_id=cases[0].case_id if cases else "C1",
        tags=cases[0].tags if cases else [],
        severity=cases[0].severity if cases else "baja",
        passed=True,
        metrics=[],
    )
    return RunReport(summary=summary, cases=[case], spec=spec)


def test_api_auth_requires_key(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "secret-key")
    monkeypatch.setattr(server, "run_engine", _fake_run_engine)
    client = TestClient(server.app)

    payload = {
        "run_id": "auto-1",
        "prompt_base": "Asistente de supermercado",
        "n_cases": 2,
        "seed": 7,
    }

    resp = client.post("/v1/auto-evaluate", json=payload)
    assert resp.status_code == 401

    resp = client.post(
        "/v1/auto-evaluate", json=payload, headers={"X-API-KEY": "secret-key"}
    )
    assert resp.status_code == 200
