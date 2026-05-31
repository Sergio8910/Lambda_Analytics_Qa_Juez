"""Tests de regresion baseline — protege corridas SIN --e2e.

Garantiza que el shape pre-e2e de ConversationPlan y ConversationResult sigue
siendo valido, y que generar_batch sin e2e_k no marca planes ni rompe el flujo
clasico (sin synthetic_context, sin artifact_expectation, sin verificador).

Comando estandar:
    python -m pytest juez/evaluation/contra_agente/tests/test_regresion_baseline.py \
        -v --tb=short -p no:xdist -p no:rerunfailures
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from juez.evaluation.contra_agente.generator import generar_batch
from juez.evaluation.contra_agente.models import (
    ConversationPlan,
    ConversationResult,
    Persona,
    TurnResult,
    TurnSpec,
)
from juez.evaluation.contra_agente import pool as pool_module
from juez.evaluation.contra_agente.pool import (
    _attach_artifact_verdict,
    _run_single_conversation,
    ejecutar_batch,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _analisis_mock() -> Dict[str, Any]:
    """Analisis minimo de agente para alimentar generar_batch sin OpenAI key."""
    return {
        "agent_id": "agente_test_regresion",
        "identidad": {"idioma": "es"},
        "prompt": {"completo": "Eres un asistente generico de prueba."},
        "tools": [
            {
                "nombre": "consultar_estado",
                "tipo": "webhook",
                "descripcion": "Consulta el estado de un pedido",
                "campos_requeridos": ["numero_pedido"],
            }
        ],
        "herramientas": [],
        "reglas_negocio": {},
    }


def _build_minimal_plan(
    plan_id: str = "conv_test_01",
    artifact_expectation=None,
    tags: Optional[List[str]] = None,
) -> ConversationPlan:
    """Construye un ConversationPlan minimo, sin artifact_expectation por default."""
    persona = Persona(
        name="UsuarioRegresion",
        mood="cordial",
        backstory="Test baseline",
        language_style="informal",
    )
    turn = TurnSpec(
        turn_id=1,
        turn_type="opener",
        intent="abrir conversacion",
        message_template="Hola, necesito ayuda con una consulta",
        success_criteria="el agente saluda",
        metrics=["task_success"],
    )
    kwargs: Dict[str, Any] = {
        "plan_id": plan_id,
        "category": "happy_path",
        "severity": "media",
        "tags": tags if tags is not None else ["happy_path"],
        "success_threshold": 0.70,
        "max_turns": 1,
        "persona": persona,
        "turns": [turn],
    }
    if artifact_expectation is not None:
        kwargs["artifact_expectation"] = artifact_expectation
    return ConversationPlan(**kwargs)


def _stub_turn_result(turn_id: int = 1) -> TurnResult:
    return TurnResult(
        turn_id=turn_id,
        turn_type="opener",
        message_sent="hola",
        agent_response="hola, en que puedo ayudarte",
        latency_ms=10.0,
        scores={"task_success": 0.9},
        passed=True,
        reason="ok",
    )


# ---------------------------------------------------------------------------
# 1. generar_batch sin e2e_k NO debe marcar planes
# ---------------------------------------------------------------------------


def test_generar_batch_sin_e2e_k_no_marca_planes():
    """Sin e2e_k, ningun plan puede tener artifact_expectation ni tag e2e_artifact."""
    batch = generar_batch(
        analisis=_analisis_mock(),
        agent_name="agente_test",
        total=5,
        concurrency=2,
        adapter="n8n",
        openai_key="",  # fuerza path heuristico
        e2e_k=0,
    )

    assert batch.total == len(batch.plans)
    assert batch.plans, "el batch heuristico debe producir al menos 1 plan"
    for plan in batch.plans:
        assert plan.artifact_expectation is None, (
            f"plan {plan.plan_id} no deberia tener artifact_expectation cuando e2e_k=0"
        )
        assert "e2e_artifact" not in plan.tags, (
            f"plan {plan.plan_id} no deberia tener tag e2e_artifact"
        )
        # tampoco source-tag de e2e
        assert not any(t.startswith("e2e_source:") for t in plan.tags), (
            f"plan {plan.plan_id} no deberia tener tag e2e_source:*"
        )


# ---------------------------------------------------------------------------
# 2. artifact_expectation y artifact_verdict son OPCIONALES
# ---------------------------------------------------------------------------


def test_conversation_plan_artifact_expectation_es_opcional():
    """ConversationPlan sin artifact_expectation se construye OK y vale None.
    ConversationResult sin artifact_verdict se construye OK y vale None.
    """
    plan = _build_minimal_plan()
    assert plan.artifact_expectation is None

    result = ConversationResult(
        plan_id="conv_test_01",
        category="happy_path",
        tags=["happy_path"],
        passed=True,
        turn_results=[_stub_turn_result()],
        collapse_turn=None,
        overall_score=0.9,
        transcript=[{"role": "user", "content": "hola"}],
        latency_total_ms=10.0,
        diagnosis="ok",
    )
    assert result.artifact_verdict is None


# ---------------------------------------------------------------------------
# 3. Plan sin artifact_expectation NO swappea el adapter por MockAdapter
# ---------------------------------------------------------------------------


def test_pool_sin_artifact_expectation_no_swap_adapter():
    """Si el plan no tiene artifact_expectation, _run_single_conversation
    debe usar el adapter pasado (no construir un MockAdapter)."""
    plan = _build_minimal_plan()

    real_adapter = MagicMock(name="real_adapter")
    real_adapter.send_message.return_value = ("respuesta del agente real", 12.3)

    evaluator = MagicMock(name="evaluator")
    evaluator.evaluate_turn.return_value = _stub_turn_result()

    # Si por error se intentara construir un MockAdapter, peta el test
    with patch.object(pool_module, "MockAdapter") as mock_adapter_cls, \
         patch.object(pool_module, "MockAgent") as mock_agent_cls, \
         patch.object(pool_module, "MockToolRunner") as mock_tool_cls, \
         patch.object(pool_module, "_attach_artifact_verdict") as attach_mock:

        result = _run_single_conversation(
            plan=plan,
            adapter=real_adapter,
            evaluator=evaluator,
            openai_key="",
            synthetic_context=None,
        )

        # el adapter real fue invocado (no fue reemplazado)
        assert real_adapter.send_message.called, (
            "el adapter pasado no fue usado — fue reemplazado indebidamente"
        )
        # nunca se construyo el camino sintetico
        mock_adapter_cls.assert_not_called()
        mock_agent_cls.assert_not_called()
        mock_tool_cls.assert_not_called()
        # _attach_artifact_verdict no se llama si no hay artifact_expectation
        attach_mock.assert_not_called()

    assert isinstance(result, ConversationResult)
    assert result.artifact_verdict is None


# ---------------------------------------------------------------------------
# 4. Serializacion: dump pre-e2e sigue siendo deserializable
# ---------------------------------------------------------------------------


def test_serializacion_plan_compat():
    """Plan.model_dump() y reconstruccion deben mantener compatibilidad pre-e2e.
    Ademas, un dump sin la llave 'artifact_expectation' debe seguir parseando OK
    (simula un dump generado antes de la feature e2e)."""
    plan = _build_minimal_plan(plan_id="conv_compat_01", tags=["happy_path"])
    dump = plan.model_dump()

    # Reconstruccion full-dump
    rebuilt = ConversationPlan(**dump)
    assert rebuilt.plan_id == plan.plan_id
    assert rebuilt.artifact_expectation is None
    assert rebuilt.tags == plan.tags
    assert rebuilt.model_dump() == dump

    # Simula dump previo a la feature e2e: borra la clave y verifica que sigue
    # siendo deserializable y vale None.
    dump_legacy = {k: v for k, v in dump.items() if k != "artifact_expectation"}
    legacy_rebuilt = ConversationPlan(**dump_legacy)
    assert legacy_rebuilt.artifact_expectation is None
    assert legacy_rebuilt.plan_id == plan.plan_id


# ---------------------------------------------------------------------------
# 5. ejecutar_batch sin synthetic_context y sin artifact_expectation
# ---------------------------------------------------------------------------


def test_ejecutar_batch_sin_synthetic_context():
    """ejecutar_batch debe correr sin synthetic_context y sin llamar
    _attach_artifact_verdict si ningun plan tiene artifact_expectation."""
    # Construye 2 planes sin artifact_expectation
    plans = [
        _build_minimal_plan(plan_id="conv_e1_01"),
        _build_minimal_plan(plan_id="conv_e1_02"),
    ]

    from juez.evaluation.contra_agente.models import ConversationBatch

    batch = ConversationBatch(
        batch_id="batch_regresion_e1",
        agent_id="agente_test_regresion",
        adapter="n8n",
        total=len(plans),
        concurrency=2,
        plans=plans,
    )

    # adapter_factory devuelve un mock que responde rapido
    def _adapter_factory(adapter_type: str, agent_id: str):
        m = MagicMock(name=f"adapter[{adapter_type}/{agent_id}]")
        m.send_message.return_value = ("respuesta cualquiera", 5.0)
        return m

    evaluator = MagicMock(name="evaluator")
    evaluator.evaluate_turn.return_value = _stub_turn_result()

    with patch.object(pool_module, "_attach_artifact_verdict") as attach_mock:
        batch_result = ejecutar_batch(
            batch=batch,
            adapter_factory=_adapter_factory,
            evaluator=evaluator,
            openai_key="",
            # synthetic_context omitido a proposito → None
        )

        attach_mock.assert_not_called()

    assert batch_result.total == 2
    assert len(batch_result.results) == 2
    for r in batch_result.results:
        assert r.artifact_verdict is None
