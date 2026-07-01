"""Test del QA de agente por transcripciones (sin LLM)."""
from __future__ import annotations

from prompt_eval.conversation import (
    ConversationInput,
    evaluate_agent_conversations,
    render_agent_qa_report_txt,
)


def _conv(cid, con_emoji):
    msg = "Hola, con gusto te ayudo 😊" if con_emoji else "Hola, dime."
    return ConversationInput(
        conversation_id=cid, agent_name="Mateo", agent_role="Asesor", language="es",
        turns=[{"turn_id": 1, "speaker": "agent", "message": msg},
               {"turn_id": 2, "speaker": "user", "message": "gracias"}],
    )


def test_qa_agente_agrega_varias():
    qa = evaluate_agent_conversations(
        "Mateo", [_conv("c1", True), _conv("c2", False)], incluir_llm=False
    )
    assert qa["agent_name"] == "Mateo"
    assert qa["n_conversaciones"] == 2
    assert 0 <= qa["score_promedio"] <= 100
    assert len(qa["por_conversacion"]) == 2
    txt = render_agent_qa_report_txt(qa)
    assert "QA DE AGENTE" in txt and "Mateo" in txt


def test_qa_agente_vacio():
    qa = evaluate_agent_conversations("X", [], incluir_llm=False)
    assert qa["n_conversaciones"] == 0
