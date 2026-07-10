"""Tests de N8nAdapter con payload_template: cuando se provee un ejemplo real
del sobre que espera el webhook (ej. WhatsApp Business API), el adapter lo usa
tal cual (sustituyendo el marcador) en vez del payload generico "shotgun".
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from juez.evaluation.contra_agente.adapters.n8n import (
    MARCADOR_MENSAJE,
    N8nAdapter,
    _sustituir_marcadores,
)


def test_sustituir_marcadores_reemplaza_en_cualquier_profundidad():
    template = {
        "entry": [{"changes": [{"value": {"messages": [{"text": {"body": MARCADOR_MENSAJE}}]}}]}],
        "otro_campo": "sin marcador",
    }
    resultado = _sustituir_marcadores(template, "hola, tengo una consulta", "sess-123")
    assert resultado["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] == "hola, tengo una consulta"
    assert resultado["otro_campo"] == "sin marcador"


def test_sustituir_marcadores_no_muta_el_original():
    template = {"body": MARCADOR_MENSAJE}
    _sustituir_marcadores(template, "mensaje real", "sess")
    assert template["body"] == MARCADOR_MENSAJE  # el original queda intacto


def test_adapter_usa_payload_template_cuando_esta_disponible():
    template = {"whatsapp_envelope": {"text": MARCADOR_MENSAJE}}
    adapter = N8nAdapter(webhook_url="https://ejemplo/webhook/x", payload_template=template)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "ok"}
    mock_resp.raise_for_status.return_value = None

    with patch("juez.evaluation.contra_agente.adapters.n8n.requests.post", return_value=mock_resp) as mock_post:
        adapter.send_message("hola, necesito ayuda", [])

    payload_enviado = mock_post.call_args.kwargs["json"]
    assert payload_enviado == {"whatsapp_envelope": {"text": "hola, necesito ayuda"}}


def test_adapter_sin_payload_template_usa_el_generico_de_siempre():
    adapter = N8nAdapter(webhook_url="https://ejemplo/webhook/x")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "ok"}
    mock_resp.raise_for_status.return_value = None

    with patch("juez.evaluation.contra_agente.adapters.n8n.requests.post", return_value=mock_resp) as mock_post:
        adapter.send_message("hola", [])

    payload_enviado = mock_post.call_args.kwargs["json"]
    assert "chatInput" in payload_enviado  # comportamiento generico previo, sin cambios
