"""El juez flaky (OpenAI rate-limit/timeout) ya NO convierte turnos buenos en
fallos. Antes devolvia 0.5 ante error/JSON-sin-score, y con threshold 0.65 eso
contaba como fallo -> un hipo de red del juez reprobaba a un agente sano. Ahora
un turno que el juez no pudo evaluar se EXCLUYE del score (evaluated=False), no
se cuenta como fallo.
"""
from __future__ import annotations

from unittest.mock import patch

import juez.evaluation.contra_agente.evaluator as ev_mod
from juez.evaluation.contra_agente.evaluator import TurnEvaluator, _eval_with_llm
from juez.evaluation.contra_agente.models import TurnResult
from juez.evaluation.contra_agente.worker import ConversationWorker
from juez.evaluation.contra_agente.tests.test_transporte_fallido import _plan


def test_eval_with_llm_devuelve_none_si_el_juez_revienta():
    def _boom(*a, **k):
        raise RuntimeError("rate limit")
    with patch.object(ev_mod, "OpenAI", create=True, side_effect=_boom):
        score, reason = _eval_with_llm("criterio", "hola", "respuesta del agente", openai_key="sk-fake")
    assert score is None
    assert "no evaluado" in reason.lower()


def test_eval_with_llm_devuelve_none_si_json_sin_score():
    class _Msg:
        content = '{"reason": "sin campo score"}'
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    class _Client:
        def __init__(self, *a, **k): pass
        @property
        def chat(self): return self
        @property
        def completions(self): return self
        def create(self, *a, **k): return _Resp()
    with patch.object(ev_mod, "OpenAI", create=True, new=_Client):
        score, _ = _eval_with_llm("criterio", "hola", "respuesta", openai_key="sk-fake")
    assert score is None


def test_turno_no_evaluado_se_excluye_del_score_no_es_fallo():
    """Un turno cuyo juez fallo (score None) queda evaluated=False y NO arrastra
    el score de la conversacion; los turnos que SI se evaluaron mandan."""
    ev = TurnEvaluator(openai_key="sk-fake", threshold=0.65)
    plan = _plan()
    ts = plan.turns[0]

    # metrica devuelve None (juez no pudo): el turno queda no-evaluado.
    with patch.object(ev, "_eval_metric", return_value=(None, "[no evaluado] rate limit")):
        turn = ev.evaluate_turn(
            turn_spec=ts, message_sent="hola", agent_response="respuesta real del agente",
            history=[], latency_ms=10.0, category="seguridad",
        )
    assert turn.evaluated is False
    assert turn.scores == {}


def test_compute_score_excluye_turnos_no_evaluados():
    """Score de la conversacion se calcula solo sobre turnos evaluados: un turno
    'no evaluado' (juez caido) no baja el promedio."""
    worker = ConversationWorker(plan=_plan(), adapter=object(), evaluator=object(), openai_key="")

    bueno = TurnResult(
        turn_id=1, turn_type="opener", message_sent="x", agent_response="y",
        latency_ms=1.0, scores={"task_success": 0.9}, passed=True, reason="ok", evaluated=True,
    )
    no_evaluado = TurnResult(
        turn_id=2, turn_type="stress", message_sent="x", agent_response="y",
        latency_ms=1.0, scores={}, passed=False, reason="[no evaluado]", evaluated=False,
    )
    # Solo cuenta el turno bueno (0.9); el no-evaluado se excluye.
    assert worker._compute_score([bueno, no_evaluado]) == 0.9
    # Sin ningun turno evaluable, no se puede puntuar -> 0.0 (visible como fallo).
    assert worker._compute_score([no_evaluado]) == 0.0


def test_transporte_caido_SI_cuenta_como_fallo_no_se_excluye():
    """Contraparte critica: un turno de TRANSPORTE caido (webhook muerto) queda
    evaluated=True y SI baja el score -- un agente inalcanzable no puede
    certificarse como seguro (distinto de un hipo del juez)."""
    worker = ConversationWorker(plan=_plan(), adapter=object(), evaluator=object(), openai_key="")
    transporte = TurnResult(
        turn_id=1, turn_type="opener", message_sent="x", agent_response="[ERROR: Timeout]",
        latency_ms=1.0, scores={"transporte": 0.0}, passed=False,
        reason="Fallo de transporte", evaluated=True,
    )
    assert worker._compute_score([transporte]) == 0.0
