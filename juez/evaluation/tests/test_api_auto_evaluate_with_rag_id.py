from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from juez.evaluation.api import server
from juez.evaluation.report_models import CaseReport, RunReport, RunSummary


def _fake_run_engine(spec, cases):
    assert any(c.retrieval_context for c in cases)
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


def test_api_auto_evaluate_with_rag_id(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "secret-key")
    monkeypatch.setattr(server, "run_engine", _fake_run_engine)
    client = TestClient(server.app)

    rag_dir = Path("RAGs")
    rag_dir.mkdir(parents=True, exist_ok=True)
    rag_path = rag_dir / "test_rag.txt"
    rag_path.write_text("Producto A: $10\nProducto B: $20", encoding="utf-8")

    payload = {
        "run_id": "auto-1",
        "prompt_base": "Asistente de supermercado",
        "n_cases": 2,
        "seed": 7,
        "rag_id": rag_path.name,
    }

    resp = client.post(
        "/v1/auto-evaluate", json=payload, headers={"X-API-KEY": "secret-key"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data.get("report", {})

    rag_path.unlink(missing_ok=True)
    if rag_dir.exists() and not any(rag_dir.iterdir()):
        rag_dir.rmdir()
