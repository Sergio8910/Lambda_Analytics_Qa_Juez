"""Tests del modulo diagnostic.

Comando estandar:
    python -m pytest juez/evaluation/contra_agente/tests/test_diagnostic.py \
        -v --tb=short -p no:xdist -p no:rerunfailures
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from juez.evaluation.contra_agente.diagnostic import analizar_diagnostico
from juez.evaluation.contra_agente.models import (
    BatchResult,
    ConversationResult,
    TurnResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turn_result(turn_id: int = 1, passed: bool = True, score: float = 0.9) -> TurnResult:
    return TurnResult(
        turn_id=turn_id,
        turn_type="opener",
        message_sent="hola",
        agent_response="hola, en que ayudo",
        latency_ms=10.0,
        scores={"task_success": score},
        passed=passed,
        reason="ok" if passed else "task_success=0.2",
    )


def _conv_result(
    plan_id: str,
    category: str,
    passed: bool,
    overall_score: float = 0.8,
    artifact_verdict: Optional[Dict[str, Any]] = None,
) -> ConversationResult:
    return ConversationResult(
        plan_id=plan_id,
        category=category,
        tags=[category],
        passed=passed,
        turn_results=[_turn_result(passed=passed, score=overall_score)],
        collapse_turn=None,
        overall_score=overall_score,
        transcript=[{"role": "user", "content": "hola"}],
        latency_total_ms=10.0,
        diagnosis="ok" if passed else "fallo",
        artifact_verdict=artifact_verdict,
    )


def _batch_result(
    results: Optional[List[ConversationResult]] = None,
    pass_rate: float = 0.7,
    total: int = 0,
    by_category: Optional[Dict[str, Dict[str, Any]]] = None,
    scorecard: Optional[Dict[str, float]] = None,
    recommendations: Optional[List[str]] = None,
    cost_summary: Optional[Dict[str, Any]] = None,
) -> BatchResult:
    results = results or []
    if total == 0:
        total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    if by_category is None:
        by_category = {}
        cat_groups: Dict[str, List[ConversationResult]] = {}
        for r in results:
            cat_groups.setdefault(r.category, []).append(r)
        for cat, items in cat_groups.items():
            p = sum(1 for x in items if x.passed)
            t = len(items)
            by_category[cat] = {
                "total": t,
                "passed": p,
                "pass_rate": (p / t) if t else 0.0,
            }
    return BatchResult(
        batch_id="batch_test",
        agent_id="agente_test",
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        by_category=by_category,
        collapse_pattern={},
        results=results,
        recommendations=recommendations or [],
        scorecard=scorecard or {"calidad_prompt": 0.7, "tools_integraciones": 0.6},
        cost_summary=cost_summary,
    )


_SECCIONES = [
    "## Veredicto",
    "## Fortalezas",
    "## Debilidades",
    "## Causa raiz",
    "## Accion recomendada",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fallback_heuristico_sin_openai_key_produce_template_completo():
    """Sin openai_key → fallback heuristico con las 5 secciones del template."""
    results = [
        _conv_result("conv_01", "happy_path", True, 0.95),
        _conv_result("conv_02", "happy_path", True, 0.92),
        _conv_result("conv_03", "herramienta", False, 0.30),
        _conv_result("conv_04", "herramienta", False, 0.25),
        _conv_result("conv_05", "seguridad", False, 0.40),
    ]
    br = _batch_result(results=results, pass_rate=0.40)

    out = analizar_diagnostico(br, openai_key="")

    assert isinstance(out, str)
    assert out.strip()
    for seccion in _SECCIONES:
        assert seccion in out, f"Falta seccion '{seccion}' en output:\n{out}"
    # Veredicto debe reflejar nivel global
    assert "40%" in out or "DEFICIENTE" in out or "REGULAR" in out


def test_openai_mockeado_retorna_contenido_mock():
    """Con OpenAI mockeado, retorna exactamente lo que devuelve el mock."""
    mock_content = (
        "## Veredicto ejecutivo\n"
        "Test mock veredicto unico ABC123.\n\n"
        "## Fortalezas\n- a\n- b\n- c\n\n"
        "## Debilidades\n- d\n- e\n- f\n\n"
        "## Causa raiz probable\nTexto raiz mock.\n\n"
        "## Accion recomendada de mayor impacto\n- accion mock"
    )

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message = MagicMock()
    fake_resp.choices[0].message.content = mock_content

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    br = _batch_result(
        results=[_conv_result("conv_01", "happy_path", True, 0.9)],
        pass_rate=0.9,
    )

    with patch(
        "juez.evaluation.contra_agente.diagnostic.OpenAI",
        return_value=fake_client,
    ):
        out = analizar_diagnostico(br, openai_key="sk-fake", model="gpt-4o-mini")

    assert "ABC123" in out
    assert "accion mock" in out
    # Verifica que se llamo a OpenAI con la key
    fake_client.chat.completions.create.assert_called_once()
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs.get("model") == "gpt-4o-mini"
    msgs = kwargs.get("messages") or []
    assert len(msgs) >= 2
    roles = [m.get("role") for m in msgs]
    assert "system" in roles and "user" in roles


def test_openai_excepcion_cae_en_fallback_heuristico():
    """Si OpenAI lanza, NO se propaga; usa fallback heuristico."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("boom")

    br = _batch_result(
        results=[
            _conv_result("conv_01", "happy_path", True, 0.9),
            _conv_result("conv_02", "herramienta", False, 0.2),
        ],
        pass_rate=0.5,
    )

    with patch(
        "juez.evaluation.contra_agente.diagnostic.OpenAI",
        return_value=fake_client,
    ):
        out = analizar_diagnostico(br, openai_key="sk-fake")

    # No debe propagar; debe retornar template heuristico
    assert isinstance(out, str)
    for seccion in _SECCIONES:
        assert seccion in out


def test_batch_vacio_no_rompe():
    """Edge: batch sin conversaciones no levanta y devuelve template."""
    br = _batch_result(
        results=[],
        pass_rate=0.0,
        total=0,
        by_category={},
        scorecard={},
    )

    out = analizar_diagnostico(br, openai_key="")

    assert isinstance(out, str)
    for seccion in _SECCIONES:
        assert seccion in out


def test_openai_devuelve_contenido_vacio_cae_en_fallback():
    """Si OpenAI devuelve content vacio, usar fallback heuristico."""
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message = MagicMock()
    fake_resp.choices[0].message.content = "   "

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    br = _batch_result(
        results=[_conv_result("conv_01", "happy_path", True, 0.9)],
        pass_rate=0.9,
    )

    with patch(
        "juez.evaluation.contra_agente.diagnostic.OpenAI",
        return_value=fake_client,
    ):
        out = analizar_diagnostico(br, openai_key="sk-fake")

    for seccion in _SECCIONES:
        assert seccion in out
