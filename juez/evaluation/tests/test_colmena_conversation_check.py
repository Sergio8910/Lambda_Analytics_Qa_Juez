"""Tests de la obrera 'conversation_check' (conversaciones REALES contra el
webhook de un flujo n8n). Todo mockeado -- estos tests NUNCA deben disparar
una peticion HTTP real ni gastar tokens; solo validan el wiring.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from juez.colmena import conversation_check as cc
from juez.colmena.project_evaluator import _load_declared_webhooks, evaluate_project_path
from juez.evaluation.contra_agente.models import BatchResult, ConversationResult, TurnResult

# Referencia a la implementacion REAL (no mockeada) antes de que el fixture
# autouse de abajo la reemplace en cada test.
_HABILITADO_EN_SERVIDOR_REAL = cc._habilitado_en_servidor


@pytest.fixture(autouse=True)
def _habilitado_en_servidor_por_defecto_en_tests(monkeypatch):
    """La mayoria de estos tests validan la LOGICA de verificar_conversaciones_reales
    una vez que el servidor ya habilito la funcion explicitamente. La salvaguarda
    en si (variable de entorno NO puesta) se prueba aparte, mas abajo, desactivando
    este fixture explicitamente."""
    monkeypatch.setattr(cc, "_habilitado_en_servidor", lambda: True)

_FLUJO = {
    "name": "notifica-cliente",
    "nodes": [
        {"id": "1", "name": "Webhook", "type": "n8n-nodes-base.webhook", "parameters": {}},
    ],
    "connections": {},
}


def _turn_result(passed: bool = True) -> TurnResult:
    return TurnResult(
        turn_id=1, turn_type="opener", message_sent="hola", agent_response="hola, como puedo ayudarte",
        latency_ms=10.0, scores={"task_success": 0.9 if passed else 0.1}, passed=passed, reason="ok",
    )


def _conv_result(plan_id: str, category: str, passed: bool) -> ConversationResult:
    return ConversationResult(
        plan_id=plan_id, category=category, tags=[category], passed=passed,
        turn_results=[_turn_result(passed)], overall_score=0.9 if passed else 0.1,
        transcript=[], latency_total_ms=10.0,
        diagnosis="todo bien" if passed else "el agente no invoco la herramienta esperada",
    )


def _batch_result(resultados: list[ConversationResult]) -> BatchResult:
    passed = sum(1 for r in resultados if r.passed)
    total = len(resultados)
    return BatchResult(
        batch_id="batch_test", agent_id="notifica-cliente", total=total, passed=passed,
        failed=total - passed, pass_rate=(passed / total if total else 0.0),
        by_category={}, collapse_pattern={}, results=resultados, recommendations=[], scorecard={},
    )


def _patch_pipeline(monkeypatch, batch_result: BatchResult):
    import juez.evaluar_n8n as evaluar_n8n_mod
    import juez.evaluation.contra_agente.generator as generator_mod
    import juez.evaluation.contra_agente.pool as pool_mod

    class _FakeAnalyzer:
        def __init__(self, workflow):
            self.workflow = workflow

        def analizar(self):
            return {"nombre": "notifica-cliente", "nodos_ia": [], "herramientas": []}

    monkeypatch.setattr(evaluar_n8n_mod, "N8nAnalyzer", _FakeAnalyzer)
    monkeypatch.setattr(
        evaluar_n8n_mod, "_convertir_analisis_para_contra_agente",
        lambda analisis_n8n: {"agent_id": "notifica-cliente", "tools": [], "prompt": {"completo": ""}},
    )
    monkeypatch.setattr(generator_mod, "generar_batch", lambda *a, **kw: SimpleNamespace(plans=[]))
    monkeypatch.setattr(pool_mod, "ejecutar_batch", lambda *a, **kw: batch_result)


def test_sin_webhook_no_hace_nada():
    assert cc.verificar_conversaciones_reales("flujo", _FLUJO, "") == []


def test_sin_openai_key_degrada_con_info(monkeypatch):
    monkeypatch.setattr(cc, "_llm_disponible", lambda: False)
    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")
    assert len(hallazgos) == 1
    assert hallazgos[0].severity == "info"
    assert "OPENAI_API_KEY" in hallazgos[0].title


def test_conversaciones_fallidas_generan_hallazgos_sin_auto_fix(monkeypatch):
    monkeypatch.setattr(cc, "_llm_disponible", lambda: True)
    resultados = [
        _conv_result("conv_01", "herramienta", passed=False),
        _conv_result("conv_02", "happy_path", passed=True),
    ]
    _patch_pipeline(monkeypatch, _batch_result(resultados))

    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")
    fallidos = [h for h in hallazgos if h.severity != "info"]
    resumen = [h for h in hallazgos if h.severity == "info"]

    assert len(fallidos) == 1
    assert fallidos[0].severity == "high"  # herramienta -> high
    assert fallidos[0].auto_fix_available is False
    assert len(resumen) == 1
    assert "1/2" in resumen[0].title


def test_todas_pasan_solo_deja_el_resumen(monkeypatch):
    monkeypatch.setattr(cc, "_llm_disponible", lambda: True)
    resultados = [_conv_result("conv_01", "happy_path", passed=True)]
    _patch_pipeline(monkeypatch, _batch_result(resultados))

    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")
    assert len(hallazgos) == 1
    assert hallazgos[0].severity == "info"


def test_error_en_el_pipeline_no_crashea(monkeypatch):
    monkeypatch.setattr(cc, "_llm_disponible", lambda: True)

    import juez.evaluar_n8n as evaluar_n8n_mod

    def _boom(workflow):
        raise RuntimeError("boom")

    monkeypatch.setattr(evaluar_n8n_mod, "N8nAnalyzer", _boom)
    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")
    assert len(hallazgos) == 1
    assert hallazgos[0].severity == "info"
    assert "error" in hallazgos[0].title.lower()


def test_load_declared_webhooks_lee_el_manifiesto():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "webhooks_n8n.json").write_text(
            json.dumps({"notifica-cliente": "https://mi-n8n.com/webhook/abc"}), encoding="utf-8",
        )
        assert _load_declared_webhooks(root) == {"notifica-cliente": "https://mi-n8n.com/webhook/abc"}


def test_load_declared_webhooks_vacio_sin_archivo():
    with tempfile.TemporaryDirectory() as tmp:
        assert _load_declared_webhooks(Path(tmp)) == {}


def test_evaluate_project_path_no_dispara_nada_sin_manifiesto(monkeypatch):
    """Sin webhooks_n8n.json, incluso con ambos flags activados, cero llamadas."""
    called = {"yes": False}

    def _fake_verificar(*a, **kw):
        called["yes"] = True
        return []

    monkeypatch.setattr(cc, "verificar_conversaciones_reales", _fake_verificar)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "flujo.json").write_text(json.dumps(_FLUJO), encoding="utf-8")
        evaluate_project_path(root, incluir_dinamicas=True, enable_real_conversations=True)
    assert called["yes"] is False


def test_evaluate_project_path_requiere_ambos_flags(monkeypatch):
    """Con manifiesto pero SIN incluir_dinamicas, tampoco dispara nada."""
    called = {"yes": False}

    def _fake_verificar(*a, **kw):
        called["yes"] = True
        return []

    monkeypatch.setattr(cc, "verificar_conversaciones_reales", _fake_verificar)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "flujo.json").write_text(json.dumps(_FLUJO), encoding="utf-8")
        (root / "webhooks_n8n.json").write_text(
            json.dumps({"notifica-cliente": "https://mi-n8n.com/webhook/abc"}), encoding="utf-8",
        )
        evaluate_project_path(root, incluir_dinamicas=False, enable_real_conversations=True)
    assert called["yes"] is False


def test_evaluate_project_path_dispara_con_ambos_flags_y_manifiesto(monkeypatch):
    def _fake_verificar(nombre, workflow, webhook_url, **kw):
        assert nombre == "notifica-cliente"
        assert webhook_url == "https://mi-n8n.com/webhook/abc"
        return []

    monkeypatch.setattr(cc, "verificar_conversaciones_reales", _fake_verificar)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "flujo.json").write_text(json.dumps(_FLUJO), encoding="utf-8")
        (root / "webhooks_n8n.json").write_text(
            json.dumps({"notifica-cliente": "https://mi-n8n.com/webhook/abc"}), encoding="utf-8",
        )
        evaluate_project_path(root, incluir_dinamicas=True, enable_real_conversations=True)


# ── Salvaguarda de servidor (COLMENA_ALLOW_REAL_CONVERSATIONS) ───────────────
# Estos tests desactivan el fixture autouse de arriba a proposito, para
# probar el comportamiento REAL por defecto (sin la variable de entorno).

def test_sin_variable_de_entorno_no_dispara_nada_pase_lo_que_pase(monkeypatch):
    """Aunque el caller (Gamma, cualquier API, etc.) pida esto explicitamente
    con un webhook real y todo disponible, sin COLMENA_ALLOW_REAL_CONVERSATIONS
    puesta en el servidor, la funcion NUNCA dispara nada real."""
    monkeypatch.setattr(cc, "_habilitado_en_servidor", _HABILITADO_EN_SERVIDOR_REAL)
    monkeypatch.delenv(cc._ENV_VAR_HABILITAR, raising=False)
    monkeypatch.setattr(cc, "_llm_disponible", lambda: True)  # incluso con LLM disponible

    llamadas_reales = {"si": False}

    def _no_deberia_llamarse(*a, **kw):
        llamadas_reales["si"] = True
        raise AssertionError("no deberia haber intentado generar/ejecutar nada")

    import juez.evaluation.contra_agente.generator as generator_mod
    monkeypatch.setattr(generator_mod, "generar_batch", _no_deberia_llamarse)

    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")

    assert llamadas_reales["si"] is False
    assert len(hallazgos) == 1
    assert hallazgos[0].severity == "info"
    assert cc._ENV_VAR_HABILITAR in hallazgos[0].description
    assert "DESACTIVADAS" in hallazgos[0].title


def test_variable_de_entorno_en_1_habilita_la_funcion(monkeypatch):
    monkeypatch.setattr(cc, "_habilitado_en_servidor", _HABILITADO_EN_SERVIDOR_REAL)
    monkeypatch.setenv(cc._ENV_VAR_HABILITAR, "1")
    monkeypatch.setattr(cc, "_llm_disponible", lambda: False)  # para llegar rapido a un resultado deterministico
    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")
    # Paso la salvaguarda (no aparece el mensaje de "DESACTIVADAS"); cae en el
    # siguiente gate (falta OPENAI_API_KEY), que es el comportamiento esperado.
    assert "DESACTIVADAS" not in hallazgos[0].title
    assert "OPENAI_API_KEY" in hallazgos[0].title


def test_variable_de_entorno_con_valor_invalido_no_habilita(monkeypatch):
    monkeypatch.setattr(cc, "_habilitado_en_servidor", _HABILITADO_EN_SERVIDOR_REAL)
    monkeypatch.setenv(cc._ENV_VAR_HABILITAR, "false")
    hallazgos = cc.verificar_conversaciones_reales("flujo", _FLUJO, "https://ejemplo/webhook/x")
    assert "DESACTIVADAS" in hallazgos[0].title
