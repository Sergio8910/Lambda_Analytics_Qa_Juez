"""Tests unitarios de la post-fase e2e del pool.

NO toca OpenAI, NO toca HTTP, NO toca BD real. Mockea:
  - `verify_inline_pdf` para evitar el Verificador.
  - `build_synthetic_pdf` cuando no nos interesa el PDF real (ahorra tiempo).
  - `MockAgent` / `MockToolRunner` / `MockAdapter` cuando se inspecciona
    `_run_single_conversation` con artifact_expectation.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from juez.evaluation.contra_agente import pool as pool_mod
from juez.evaluation.contra_agente.models import (
    ArtifactExpectation,
    ConversationPlan,
    ConversationResult,
    Persona,
    TurnSpec,
)
from juez.evaluation.contra_agente.pool import (
    _attach_artifact_verdict,
    _run_single_conversation,
)
from juez.evaluation.contra_agente.verificador_client import VerificadorUnavailable


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_plan_with_expectation(
    with_expectation: bool = True,
    plan_id: str = "conv_01",
    weight: float = 0.30,
    threshold: float = 0.70,
) -> ConversationPlan:
    """Construye un plan con o sin artifact_expectation."""
    expectation = None
    if with_expectation:
        artifact_id = "JUEZ-E2E-TEST-01"
        expectation = ArtifactExpectation(
            artifact_type="pdf",
            cliente="abad_synthetic",
            artifact_id=artifact_id,
            weight=weight,
            expected_snapshot={
                "artifact_id": artifact_id,
                "counts": {"fotos": 5, "ambientes": 1},
                "structure": {
                    "ambientes": ["Sala"],
                    "fotos_por_ambiente": {"Sala": 5},
                    "tipo_inventario": "INICIAL",
                },
                "required_strings": [artifact_id, "Propietario", "INICIAL"],
            },
            canonical_data={
                "source": "synthetic",
                "contrato_id": artifact_id,
                "inventario_id": 99001,
                "propietario": "Propietario",
                "arrendatario": "Arrendatario",
                "tipo_inventario": "INICIAL",
                "ambientes": ["Sala"],
                "fotos_por_ambiente": {"Sala": 5},
                "total_fotos": 5,
            },
        )

    return ConversationPlan(
        plan_id=plan_id,
        category="happy_path",
        severity="media",
        tags=["happy_path"],
        success_threshold=threshold,
        max_turns=2,
        persona=Persona(
            name="Test User",
            mood="cordial",
            backstory="test",
            language_style="informal",
        ),
        turns=[
            TurnSpec(
                turn_id=1,
                turn_type="opener",
                intent="saludar",
                message_template="Hola",
                success_criteria="responde",
                metrics=["task_success"],
            ),
        ],
        artifact_expectation=expectation,
    )


def _make_result(
    plan: ConversationPlan,
    overall_score: float = 0.80,
    passed: bool = True,
) -> ConversationResult:
    """Construye un ConversationResult dummy."""
    return ConversationResult(
        plan_id=plan.plan_id,
        category=plan.category,
        tags=list(plan.tags),
        passed=passed,
        turn_results=[],
        collapse_turn=None,
        overall_score=overall_score,
        transcript=[],
        latency_total_ms=10.0,
        diagnosis="ok",
    )


# ── Tests de _attach_artifact_verdict ────────────────────────────────────────
def test_attach_calls_build_pdf_when_tool_runner_calls_empty():
    """Con tool_runner.calls vacio -> llama a build_synthetic_pdf de todos modos."""
    plan = _make_plan_with_expectation()
    result = _make_result(plan, overall_score=0.80)
    tool_runner = MagicMock()
    tool_runner.calls = []

    fake_verdict = {"status": "completed", "verdict": "ok", "score": 1.0}

    with patch.object(pool_mod, "build_synthetic_pdf", return_value=b"%PDF-fake") as bsp, \
         patch.object(pool_mod, "verify_inline_pdf", return_value=fake_verdict):
        out = _attach_artifact_verdict(result, plan, tool_runner)

    bsp.assert_called_once()
    # Primer arg posicional: canonical_data; segundo: calls (lista vacia)
    args, kwargs = bsp.call_args
    assert args[0] == plan.artifact_expectation.canonical_data
    assert args[1] == []
    assert out.artifact_verdict == fake_verdict


def test_attach_mixes_score_on_ok_verdict():
    """Verdict OK score=1.0 -> overall_score = old*(1-w) + 1.0*w."""
    plan = _make_plan_with_expectation(weight=0.30, threshold=0.70)
    result = _make_result(plan, overall_score=0.60, passed=False)

    fake_verdict = {"status": "completed", "verdict": "ok", "score": 1.0}
    with patch.object(pool_mod, "build_synthetic_pdf", return_value=b"%PDF-fake"), \
         patch.object(pool_mod, "verify_inline_pdf", return_value=fake_verdict):
        out = _attach_artifact_verdict(result, plan, MagicMock(calls=[]))

    # 0.60 * 0.70 + 1.0 * 0.30 = 0.42 + 0.30 = 0.72
    assert out.overall_score == pytest.approx(0.72, abs=0.001)
    # 0.72 >= threshold 0.70 -> passed = True
    assert out.passed is True
    assert out.artifact_verdict == fake_verdict


def test_attach_verificador_unavailable_skips():
    """VerificadorUnavailable -> status=skipped, skip_reason=verificador_unavailable,
    overall_score NO cambia."""
    plan = _make_plan_with_expectation()
    result = _make_result(plan, overall_score=0.85, passed=True)
    original_score = result.overall_score
    original_passed = result.passed

    with patch.object(pool_mod, "build_synthetic_pdf", return_value=b"%PDF-fake"), \
         patch.object(
            pool_mod, "verify_inline_pdf",
            side_effect=VerificadorUnavailable("boom"),
         ):
        out = _attach_artifact_verdict(result, plan, MagicMock(calls=[]))

    assert out.artifact_verdict is not None
    assert out.artifact_verdict["status"] == "skipped"
    assert out.artifact_verdict["skip_reason"] == "verificador_unavailable"
    # overall_score NO debe haber cambiado
    assert out.overall_score == original_score
    assert out.passed == original_passed


def test_attach_generic_exception_skips_with_postphase_error():
    """Exception generica -> status=skipped, skip_reason=artifact_postphase_error."""
    plan = _make_plan_with_expectation()
    result = _make_result(plan, overall_score=0.55)
    original_score = result.overall_score

    with patch.object(pool_mod, "build_synthetic_pdf", return_value=b"%PDF-fake"), \
         patch.object(
            pool_mod, "verify_inline_pdf",
            side_effect=ValueError("oops generic"),
         ):
        out = _attach_artifact_verdict(result, plan, MagicMock(calls=[]))

    assert out.artifact_verdict is not None
    assert out.artifact_verdict["status"] == "skipped"
    assert out.artifact_verdict["skip_reason"] == "artifact_postphase_error"
    # overall_score NO debe haber cambiado
    assert out.overall_score == original_score


# ── Tests de _run_single_conversation ────────────────────────────────────────
def test_run_single_no_expectation_uses_given_adapter():
    """Plan sin artifact_expectation -> usa el adapter pasado, sin swap."""
    plan = _make_plan_with_expectation(with_expectation=False)
    given_adapter = MagicMock(name="given_adapter")

    evaluator = MagicMock()
    fake_result = _make_result(plan, overall_score=0.5)

    # Patch ConversationWorker para capturar que adapter recibe.
    with patch.object(pool_mod, "ConversationWorker") as CW:
        worker_inst = MagicMock()
        worker_inst.run.return_value = fake_result
        CW.return_value = worker_inst

        out = _run_single_conversation(
            plan=plan,
            adapter=given_adapter,
            evaluator=evaluator,
            openai_key="",
            synthetic_context=None,
        )

    # No debe haber swap a MockAdapter -> el ConversationWorker recibe
    # exactamente el adapter pasado.
    assert CW.call_count == 1
    _, kwargs = CW.call_args
    assert kwargs["adapter"] is given_adapter
    # Sin expectation -> result devuelto sin tocar artifact_verdict
    assert out.artifact_verdict is None
    assert out.plan_id == plan.plan_id


def test_run_single_with_expectation_no_key_returns_error():
    """Con artifact_expectation y openai_key vacio -> ERROR tecnico (capturado
    por _error_result)."""
    plan = _make_plan_with_expectation(with_expectation=True)
    evaluator = MagicMock()

    out = _run_single_conversation(
        plan=plan,
        adapter=MagicMock(),
        evaluator=evaluator,
        openai_key="",
        synthetic_context=None,
    )

    assert out.passed is False
    assert out.overall_score == 0.0
    assert "Error" in out.diagnosis or "error" in out.diagnosis.lower()
    # No deberia haberse setteado artifact_verdict
    assert out.artifact_verdict is None


def test_run_single_with_expectation_instances_mocks_and_swaps_adapter():
    """Con artifact_expectation y openai_key set -> instancia MockToolRunner +
    MockAgent + MockAdapter (swap del adapter pasado)."""
    plan = _make_plan_with_expectation(with_expectation=True)
    given_adapter = MagicMock(name="given_adapter")
    evaluator = MagicMock()

    fake_result = _make_result(plan, overall_score=0.50)

    # Mockear todas las piezas que el codigo instancia.
    with patch.object(pool_mod, "MockToolRunner") as MTR, \
         patch.object(pool_mod, "MockAgent") as MA, \
         patch.object(pool_mod, "MockAdapter") as MAD, \
         patch.object(pool_mod, "ConversationWorker") as CW, \
         patch.object(pool_mod, "build_synthetic_pdf", return_value=b"%PDF-fake"), \
         patch.object(
            pool_mod, "verify_inline_pdf",
            return_value={"status": "completed", "verdict": "ok", "score": 1.0},
         ):
        tool_runner_inst = MagicMock()
        tool_runner_inst.calls = []
        MTR.return_value = tool_runner_inst

        mock_agent = MagicMock()
        mock_agent.respond = MagicMock(return_value="ok")
        MA.return_value = mock_agent

        mock_adapter = MagicMock(name="mock_adapter_inst")
        MAD.return_value = mock_adapter

        worker_inst = MagicMock()
        worker_inst.run.return_value = fake_result
        CW.return_value = worker_inst

        synthetic_context = {
            "system_prompt": "Eres un agente.",
            "herramientas": [{"nombre": "tool_x"}],
            "model": "gpt-4o-mini",
        }
        out = _run_single_conversation(
            plan=plan,
            adapter=given_adapter,
            evaluator=evaluator,
            openai_key="sk-fake-key",
            synthetic_context=synthetic_context,
        )

    # 1) Se instanciaron MockToolRunner, MockAgent y MockAdapter
    MTR.assert_called_once_with(plan.artifact_expectation.canonical_data)
    MA.assert_called_once()
    _, ma_kwargs = MA.call_args
    assert ma_kwargs["system_prompt"] == "Eres un agente."
    assert ma_kwargs["herramientas"] == [{"nombre": "tool_x"}]
    assert ma_kwargs["model"] == "gpt-4o-mini"
    assert ma_kwargs["openai_key"] == "sk-fake-key"

    MAD.assert_called_once_with(agent=mock_agent, tool_runner=tool_runner_inst)

    # 2) El ConversationWorker recibio el MockAdapter swappeado, NO el given_adapter.
    _, cw_kwargs = CW.call_args
    assert cw_kwargs["adapter"] is mock_adapter
    assert cw_kwargs["adapter"] is not given_adapter

    # 3) artifact_verdict quedo poblado con el resultado del verificador mockeado.
    #    Tras el cambio del reporter side-by-side, el pool tambien adjunta
    #    `expected_snapshot` al verdict para que el reporter pueda renderizar
    #    la tabla esperado-vs-observado. Validamos subset y el snapshot aparte.
    av = out.artifact_verdict
    assert av is not None
    assert av["status"] == "completed"
    assert av["verdict"] == "ok"
    assert av["score"] == 1.0
    # El snapshot esperado del plan tiene que aparecer en el verdict.
    assert av["expected_snapshot"] == plan.artifact_expectation.expected_snapshot
