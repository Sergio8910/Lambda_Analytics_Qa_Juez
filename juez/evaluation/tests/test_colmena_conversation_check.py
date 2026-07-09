"""Tests de la obrera 'conversation_check' (conversaciones REALES contra el
webhook de un flujo n8n). Todo mockeado -- estos tests NUNCA deben disparar
una peticion HTTP real ni gastar tokens; solo validan el wiring.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from juez.colmena import conversation_check as cc
from juez.colmena.project_evaluator import _load_declared_webhooks, evaluate_project_path
from juez.evaluation.contra_agente.models import BatchResult, ConversationResult, TurnResult

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
