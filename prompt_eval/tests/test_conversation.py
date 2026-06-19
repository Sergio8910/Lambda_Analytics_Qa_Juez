"""Tests de la evaluación de conversaciones (transcript -> evaluación)."""
from __future__ import annotations

from prompt_eval.conversation import (
    ConversationInput,
    deterministic_checks,
    evaluate_conversation,
    render_conversation_report,
)


def _conv_mateo() -> ConversationInput:
    return ConversationInput(
        conversation_id="conv_mateo_001",
        platform="whatsapp",
        agent_name="Mateo",
        agent_role="Asesor de celulares",
        language="es-CO",
        turns=[
            {"turn_id": 1, "timestamp": "2026-05-19T14:30:00Z", "speaker": "agent",
             "message": "¡Hola! Soy Mateo, tu asesor 📱 ¿Qué duda tienes hoy? 👂"},
            {"turn_id": 2, "timestamp": "2026-05-19T14:30:15Z", "speaker": "user",
             "message": "No sé cómo activar el Face ID de mi iPhone 12."},
            {"turn_id": 3, "timestamp": "2026-05-19T14:30:45Z", "speaker": "agent",
             "message": "Buena pregunta 🤔 ¿No te reconoce la cara o sale un error?"},
            {"turn_id": 4, "timestamp": "2026-05-19T14:31:20Z", "speaker": "user",
             "message": "Dice que no está disponible temporalmente."},
            {"turn_id": 5, "timestamp": "2026-05-19T14:32:00Z", "speaker": "agent",
             "message": "Reinicia el iPhone 💪 Si sigue, eso ya escapa a lo mío, mejor ve a Apple 😅"},
        ],
        conversation_metadata={"resolution_status": "resolved"},
    )


def test_deterministic_checks():
    met = deterministic_checks(_conv_mateo())
    assert met["total_turnos"] == 5
    assert met["turnos_agente"] == 3
    assert met["turnos_usuario"] == 2
    assert met["emojis_totales"] >= 3
    assert met["turnos_agente_con_pregunta"] >= 1
    assert met["tiempo_respuesta_promedio_s"] is not None
    assert met["idioma_esperado"] == "es"


def test_evaluate_sin_llm():
    res = evaluate_conversation(_conv_mateo(), incluir_llm=False)
    assert res.conversation_id == "conv_mateo_001"
    assert res.agent_name == "Mateo"
    assert res.llm_judge_aplicado is False
    assert 0 <= res.score_global <= 100
    # criterios determinísticos verificados de forma independiente
    nombres = {c.nombre for c in res.criterios}
    assert "uso_de_emojis" in nombres
    assert "preguntas_de_aclaracion" in nombres
    emoji = next(c for c in res.criterios if c.nombre == "uso_de_emojis")
    assert emoji.cumple is True  # Mateo usa emojis


def test_reporte_txt():
    res = evaluate_conversation(_conv_mateo(), incluir_llm=False)
    txt = render_conversation_report(res)
    assert "EVALUACIÓN DE CONVERSACIÓN" in txt
    assert "Mateo" in txt
    assert "Asesor de celulares" in txt
    assert "Veredicto" in txt
    assert "Métricas" in txt


def test_conversacion_sin_emojis_no_cumple():
    conv = ConversationInput(
        conversation_id="x", agent_name="A", agent_role="Soporte", language="es",
        turns=[
            {"turn_id": 1, "speaker": "agent", "message": "Buenas, dime."},
            {"turn_id": 2, "speaker": "user", "message": "Hola"},
        ],
    )
    res = evaluate_conversation(conv, incluir_llm=False)
    emoji = next(c for c in res.criterios if c.nombre == "uso_de_emojis")
    assert emoji.cumple is False
