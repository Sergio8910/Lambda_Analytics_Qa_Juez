"""Tests del HTML reporter.

Comando estandar:
    python -m pytest juez/evaluation/contra_agente/tests/test_html_reporter.py \
        -v --tb=short -p no:xdist -p no:rerunfailures
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

import pytest

from juez.evaluation.contra_agente.html_reporter import generar_reporte_html
from juez.evaluation.contra_agente.models import (
    BatchResult,
    ConversationResult,
    TurnResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StrictHTMLParser(HTMLParser):
    """Parser que falla si encuentra errores de markup."""

    def __init__(self) -> None:
        super().__init__()
        self.errors: List[str] = []
        self.titles: List[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.titles.append(data)

    def error(self, message: str) -> None:  # noqa: D401 - signature de HTMLParser
        self.errors.append(message)


def _parse_html(html_str: str) -> _StrictHTMLParser:
    parser = _StrictHTMLParser()
    parser.feed(html_str)
    parser.close()
    return parser


def _turn_result(passed: bool = True) -> TurnResult:
    return TurnResult(
        turn_id=1,
        turn_type="opener",
        message_sent="hola",
        agent_response="hola, en que ayudo",
        latency_ms=10.0,
        scores={"task_success": 0.9 if passed else 0.3},
        passed=passed,
        reason="ok" if passed else "task_success=0.3",
    )


def _conv_result(
    plan_id: str = "conv_01",
    category: str = "happy_path",
    passed: bool = True,
    overall_score: float = 0.9,
    artifact_verdict: Optional[Dict[str, Any]] = None,
) -> ConversationResult:
    return ConversationResult(
        plan_id=plan_id,
        category=category,
        tags=[category],
        passed=passed,
        turn_results=[_turn_result(passed=passed)],
        collapse_turn=None,
        overall_score=overall_score,
        transcript=[{"role": "user", "content": "hola"}],
        latency_total_ms=10.0,
        diagnosis="ok",
        artifact_verdict=artifact_verdict,
    )


def _batch_result(
    pass_rate: float = 0.85,
    results: Optional[List[ConversationResult]] = None,
    scorecard: Optional[Dict[str, float]] = None,
    cost_summary: Optional[Dict[str, Any]] = None,
) -> BatchResult:
    results = results or [
        _conv_result("conv_01", "happy_path", True, 0.95),
        _conv_result("conv_02", "happy_path", True, 0.90),
    ]
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    return BatchResult(
        batch_id="batch_test",
        agent_id="agente_demo",
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=pass_rate,
        by_category={"happy_path": {"total": total, "passed": passed, "pass_rate": pass_rate}},
        collapse_pattern={},
        results=results,
        recommendations=[],
        scorecard=scorecard or {"calidad_prompt": 0.85, "tools_integraciones": 0.78},
        cost_summary=cost_summary,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_html_es_parseable():
    """Output debe parsearse con html.parser sin errores."""
    br = _batch_result()
    out = generar_reporte_html(br, agent_name="Agente Demo")
    parser = _parse_html(out)
    assert parser.errors == []
    # Debe comenzar con doctype y tener html
    assert out.lstrip().lower().startswith("<!doctype html>")
    assert "<html" in out and "</html>" in out


def test_title_contiene_agent_name():
    """El <title> debe contener el agent_name pasado."""
    br = _batch_result()
    out = generar_reporte_html(br, agent_name="MiAgenteEspecial")
    parser = _parse_html(out)
    title_text = " ".join(parser.titles)
    assert "MiAgenteEspecial" in title_text


def test_pass_rate_85_aparece_en_output():
    """Con pass_rate=0.85 debe aparecer '85' o '85%' en el HTML."""
    br = _batch_result(pass_rate=0.85)
    out = generar_reporte_html(br, agent_name="Demo")
    assert "85%" in out or "85" in out


def test_seccion_e2e_aparece_si_hay_artifact_verdict_ok():
    """Si hay artifact_verdict OK, la seccion e2e debe aparecer."""
    verdict = {
        "status": "completed",
        "verdict": "OK",
        "score": 0.92,
        "elapsed_ms": 1234,
        "artifact_id": "JUEZ-E2E-01",
        "checks": [
            {"name": "fotos_count", "verdict": "OK", "score": 1.0},
            {"name": "estructura", "verdict": "OK", "score": 0.95},
        ],
    }
    cr = _conv_result("conv_e2e", "happy_path", True, 0.9, artifact_verdict=verdict)
    br = _batch_result(results=[cr])
    out = generar_reporte_html(br, agent_name="Demo")
    assert "Verificacion e2e" in out
    assert "JUEZ-E2E-01" in out
    assert "fotos_count" in out


def test_batch_vacio_no_crashea():
    """Sin results, scorecard vacio, etc., debe retornar HTML valido aunque limitado."""
    br = BatchResult(
        batch_id="b",
        agent_id="a",
        total=0,
        passed=0,
        failed=0,
        pass_rate=0.0,
        by_category={},
        collapse_pattern={},
        results=[],
        recommendations=[],
        scorecard={},
        cost_summary=None,
    )
    out = generar_reporte_html(br, agent_name="VacioTest")
    parser = _parse_html(out)
    assert parser.errors == []
    assert "VacioTest" in out


def test_diagnostic_text_aparece_en_output():
    """Si se pasa diagnostic_text, debe aparecer en el HTML."""
    br = _batch_result()
    diagnostic = (
        "## Veredicto ejecutivo\nTextoUnicoXYZ987.\n\n## Fortalezas\n- a"
    )
    out = generar_reporte_html(br, diagnostic_text=diagnostic, agent_name="Demo")
    assert "TextoUnicoXYZ987" in out
    assert "Diagnostico" in out or "diagnostico" in out


def test_cost_summary_se_muestra_si_existe():
    """Si batch_result.cost_summary existe, debe mostrar tokens."""
    cost = {
        "total_tokens": 12345,
        "total_cost_usd": 0.1234,
        "total_calls": 7,
        "by_model": {
            "gpt-4o-mini": {
                "prompt_tokens": 10000,
                "completion_tokens": 2345,
                "calls": 7,
                "cost_usd": 0.1234,
            }
        },
    }
    br = _batch_result(cost_summary=cost)
    out = generar_reporte_html(br, agent_name="Demo")
    assert "12345" in out or "12,345" in out
    assert "gpt-4o-mini" in out


def test_analisis_problemas_se_muestran():
    """Si se pasa analisis con problemas, deben aparecer en la seccion correspondiente."""
    analisis = {
        "problemas": [
            {"severidad": "alta", "mensaje": "Problema critico MARCA_ALTA"},
            {"severidad": "baja", "mensaje": "Problema menor MARCA_BAJA"},
        ]
    }
    br = _batch_result()
    out = generar_reporte_html(br, analisis=analisis, agent_name="Demo")
    assert "MARCA_ALTA" in out
    assert "MARCA_BAJA" in out
    assert "Problemas" in out or "problemas" in out


def test_batch_none_no_crashea():
    """Si batch_result es None, no debe crashear (retorna HTML basico)."""
    out = generar_reporte_html(None, agent_name="SoloNombre")
    parser = _parse_html(out)
    assert parser.errors == []
    assert "SoloNombre" in out
