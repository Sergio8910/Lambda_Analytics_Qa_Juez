"""Tests de la auto-inferencia del "sobre" de entrada de un flujo n8n.

Verifica que el Juez pueda enviar el mensaje en la ruta que el flujo realmente
lee (body.message, chatInput, etc.) SIN configuracion manual, y que el hint se
fusione sobre el payload generico sin perder los alias planos.
"""
import importlib.util as ilu
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_spec = ilu.spec_from_file_location("evaluar_n8n", _ROOT / "juez" / "evaluar_n8n.py")
_m = ilu.module_from_spec(_spec)
_spec.loader.exec_module(_m)

from juez.evaluation.contra_agente.adapters.n8n import (  # noqa: E402
    MARCADOR_MENSAJE,
    MARCADOR_SESSION,
    N8nAdapter,
)


def test_infiere_sobre_whatsapp_body():
    wf = {
        "name": "WA",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.set", "name": "Extract", "parameters": {
                "assignments": {"assignments": [
                    {"name": "texto", "value": "={{ $json.body.message }}"},
                    {"name": "sid", "value": "={{ $json.body.sessionId }}"},
                ]}}},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert env["body"]["message"] == MARCADOR_MENSAJE
    assert env["body"]["sessionId"] == MARCADOR_SESSION


def test_infiere_chat_trigger_chatinput():
    wf = {
        "name": "Chat",
        "nodes": [
            {"type": "@n8n/n8n-nodes-langchain.chatTrigger", "name": "Chat", "parameters": {}},
            {"type": "@n8n/n8n-nodes-langchain.agent", "name": "AI",
             "parameters": {"text": "={{ $json.chatInput }}"}},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert env.get("chatInput") == MARCADOR_MENSAJE


def test_sin_webhook_no_infiere():
    wf = {"name": "cron", "nodes": [
        {"type": "n8n-nodes-base.scheduleTrigger", "name": "S", "parameters": {}}
    ], "connections": {}}
    assert _m.inferir_envelope_desde_wf(wf) is None


def test_garantia_mensaje_siempre_presente():
    """Un webhook sin refs claras igual recibe el texto por rutas comunes."""
    wf = {"name": "raw", "nodes": [
        {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}}
    ], "connections": {}}
    env = _m.inferir_envelope_desde_wf(wf)
    assert MARCADOR_MENSAJE in json.dumps(env)


def test_adapter_fusiona_envelope_y_conserva_alias_plano():
    env = {"body": {"message": MARCADOR_MENSAJE, "sessionId": MARCADOR_SESSION}}
    a = N8nAdapter(webhook_url="http://x", envelope_hint=env)
    payload = a._build_payload("hola quiero una cita", [])
    # El texto llega por la ruta anidada que el flujo lee...
    assert payload["body"]["message"] == "hola quiero una cita"
    # ...y se conservan los alias planos del payload generico.
    assert payload["message"] == "hola quiero una cita"
    assert payload["chatInput"] == "hola quiero una cita"


def test_payload_template_explicito_ignora_envelope_hint():
    """Si el usuario da un payload_template, manda ese; el hint no interfiere."""
    tmpl = {"custom": {"txt": MARCADOR_MENSAJE}}
    a = N8nAdapter(webhook_url="http://x", payload_template=tmpl,
                   envelope_hint={"body": {"message": MARCADOR_MENSAJE}})
    # send_message usa payload_template tal cual (via _sustituir_marcadores);
    # el envelope_hint solo aplica en el payload generico (_build_payload).
    assert a.payload_template == tmpl
