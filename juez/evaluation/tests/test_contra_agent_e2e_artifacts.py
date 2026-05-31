from __future__ import annotations

from juez.evaluation.contra_agente.generator import generar_batch
from juez.evaluation.contra_agente.models import (
    ArtifactExpectation,
    ConversationPlan,
    ConversationResult,
    Persona,
)
from juez.evaluation.contra_agente.pool import _attach_artifact_verdict
from juez.evaluation.contra_agente.reporter import generar_reporte_batch
from juez.evaluation.contra_agente.models import BatchResult
from juez.evaluation.contra_agente.verificador_client import (
    VerificadorUnavailable,
    healthcheck,
    verify_inline_pdf,
)


def test_generar_batch_marca_un_happy_path_e2e() -> None:
    batch = generar_batch(
        analisis={"agent_id": "abad", "prompt": {"completo": "Eres agente de inventarios."}},
        agent_name="Abad",
        total=3,
        concurrency=1,
        openai_key="",
        distribucion_override={"happy_path": 1, "herramienta": 2},
        e2e_k=1,
    )

    marked = [p for p in batch.plans if p.artifact_expectation is not None]

    assert len(marked) == 1
    assert marked[0].category == "happy_path"
    assert "e2e_artifact" in marked[0].tags
    assert marked[0].artifact_expectation is not None
    assert marked[0].artifact_expectation.artifact_id in marked[0].turns[0].message_template


def test_generar_batch_sin_e2e_no_marca_planes() -> None:
    batch = generar_batch(
        analisis={"agent_id": "abad", "prompt": {"completo": "Eres agente de inventarios."}},
        agent_name="Abad",
        total=2,
        concurrency=1,
        openai_key="",
        distribucion_override={"happy_path": 1, "herramienta": 1},
    )

    assert all(p.artifact_expectation is None for p in batch.plans)


def test_attach_artifact_verdict_mezcla_score(monkeypatch) -> None:
    expectation = ArtifactExpectation(
        artifact_id="JUEZ-E2E-1",
        weight=0.25,
        expected_snapshot={"artifact_id": "JUEZ-E2E-1"},
        canonical_data={"contrato_id": "JUEZ-E2E-1"},
    )
    plan = ConversationPlan(
        plan_id="conv_01",
        category="happy_path",
        tags=["happy_path"],
        max_turns=1,
        persona=Persona(name="Ana", mood="cordial", backstory="test"),
        turns=[],
        artifact_expectation=expectation,
    )
    result = ConversationResult(
        plan_id="conv_01",
        category="happy_path",
        tags=["happy_path"],
        passed=True,
        turn_results=[],
        overall_score=0.8,
        transcript=[],
        latency_total_ms=10,
        diagnosis="ok",
    )

    monkeypatch.setattr(
        "juez.evaluation.contra_agente.pool.build_synthetic_pdf",
        lambda canonical, calls: b"%PDF",
    )
    monkeypatch.setattr(
        "juez.evaluation.contra_agente.pool.verify_inline_pdf",
        lambda **kwargs: {
            "status": "completed",
            "verdict": "OK",
            "score": 1.0,
            "checks": [],
            "issues": [],
            "artifact_id": kwargs["artifact_id"],
            "elapsed_ms": 12,
        },
    )

    mixed = _attach_artifact_verdict(result, plan, tool_runner=None)

    assert mixed.overall_score == 0.85
    assert mixed.passed is True
    assert mixed.artifact_verdict["verdict"] == "OK"


def test_attach_artifact_verdict_skip_si_verificador_no_responde(monkeypatch) -> None:
    expectation = ArtifactExpectation(
        artifact_id="JUEZ-E2E-2",
        expected_snapshot={"artifact_id": "JUEZ-E2E-2"},
        canonical_data={"contrato_id": "JUEZ-E2E-2"},
    )
    plan = ConversationPlan(
        plan_id="conv_02",
        category="happy_path",
        tags=["happy_path"],
        max_turns=1,
        persona=Persona(name="Ana", mood="cordial", backstory="test"),
        turns=[],
        artifact_expectation=expectation,
    )
    result = ConversationResult(
        plan_id="conv_02",
        category="happy_path",
        tags=["happy_path"],
        passed=True,
        turn_results=[],
        overall_score=0.77,
        transcript=[],
        latency_total_ms=10,
        diagnosis="ok",
    )

    monkeypatch.setattr(
        "juez.evaluation.contra_agente.pool.build_synthetic_pdf",
        lambda canonical, calls: b"%PDF",
    )

    def _raise(**kwargs):
        raise VerificadorUnavailable("down")

    monkeypatch.setattr("juez.evaluation.contra_agente.pool.verify_inline_pdf", _raise)

    skipped = _attach_artifact_verdict(result, plan, tool_runner=None)

    assert skipped.overall_score == 0.77
    assert skipped.artifact_verdict["status"] == "skipped"
    assert skipped.artifact_verdict["skip_reason"] == "verificador_unavailable"


def test_reporte_incluye_seccion_e2e() -> None:
    result = ConversationResult(
        plan_id="conv_01",
        category="happy_path",
        tags=["happy_path"],
        passed=True,
        turn_results=[],
        overall_score=1.0,
        transcript=[],
        latency_total_ms=10,
        diagnosis="ok",
        artifact_verdict={
            "status": "completed",
            "verdict": "OK",
            "score": 1.0,
            "checks": [{"name": "integridad", "verdict": "OK", "score": 1.0}],
            "issues": [],
            "artifact_id": "JUEZ-E2E-1",
            "elapsed_ms": 12,
        },
    )
    batch = BatchResult(
        batch_id="batch_1",
        agent_id="abad",
        total=1,
        passed=1,
        failed=0,
        pass_rate=1.0,
        by_category={"happy_path": {"total": 1, "passed": 1, "pass_rate": 1.0}},
        collapse_pattern={},
        results=[result],
        recommendations=[],
        scorecard={},
    )

    report = generar_reporte_batch(batch, agent_name="Abad")

    assert "VERIFICACION E2E DE ARTEFACTOS" in report
    assert "integridad" in report
    assert "JUEZ-E2E-1" in report


def test_verificador_client_healthcheck_ok(monkeypatch) -> None:
    class Response:
        status_code = 200

    monkeypatch.setattr(
        "juez.evaluation.contra_agente.verificador_client.requests.get",
        lambda url, timeout: Response(),
    )

    assert healthcheck(base_url="http://verificador.test", timeout_s=0.1) is True


def test_verificador_client_dispatch_y_polling(monkeypatch) -> None:
    calls = {"get": 0, "post_body": None}

    class Response:
        def __init__(self, status_code: int, payload: dict) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self) -> dict:
            return self._payload

    def fake_post(url, json, headers, timeout):
        calls["post_body"] = json
        return Response(202, {"verification_id": "verif_123", "status": "queued"})

    def fake_get(url, headers, timeout):
        calls["get"] += 1
        if calls["get"] == 1:
            return Response(200, {"verification_id": "verif_123", "status": "running"})
        return Response(
            200,
            {
                "verification_id": "verif_123",
                "status": "completed",
                "verdict": "OK",
                "score": 1.0,
                "checks": [],
                "issues": [],
            },
        )

    monkeypatch.setattr("juez.evaluation.contra_agente.verificador_client.requests.post", fake_post)
    monkeypatch.setattr("juez.evaluation.contra_agente.verificador_client.requests.get", fake_get)
    monkeypatch.setattr("juez.evaluation.contra_agente.verificador_client.time.sleep", lambda _: None)

    result = verify_inline_pdf(
        cliente="abad_synthetic",
        artifact_id="JUEZ-E2E-HTTP",
        pdf_bytes=b"%PDF",
        expected_snapshot={"artifact_id": "JUEZ-E2E-HTTP"},
        base_url="http://verificador.test",
        poll_timeout_s=1,
        poll_interval_s=0.01,
    )

    assert result["status"] == "completed"
    assert result["score"] == 1.0
    assert calls["get"] == 2
    assert calls["post_body"]["source"]["type"] == "inline"
    assert calls["post_body"]["metadata"]["expected_snapshot"]["artifact_id"] == "JUEZ-E2E-HTTP"
