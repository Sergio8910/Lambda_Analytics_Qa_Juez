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
    # n8n envuelve el POST bajo `body`; el flujo lee `$json.body.message`, pero
    # el Juez debe POSTear el campo al nivel superior (n8n añade el `body` solo).
    assert env["message"] == MARCADOR_MENSAJE
    assert env["sessionId"] == MARCADOR_SESSION
    assert "body" not in env


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


def test_switch_v3_message_type_toma_rama_text():
    """Un Switch que enruta por body.MessageType debe recibir un valor que tome
    una rama valida (text), no el default generico que detiene el flujo."""
    wf = {
        "name": "Router",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook - Entrante", "parameters": {}},
            {"type": "n8n-nodes-base.switch", "name": "Route Types1", "parameters": {
                "mode": "rules",
                "rules": {"values": [
                    {"conditions": {"combinator": "and", "conditions": [
                        {"leftValue": "={{ $('Webhook - Entrante').item.json.body.MessageType }}",
                         "rightValue": "text",
                         "operator": {"type": "string", "operation": "equals"}}
                    ]}, "outputKey": "Text"},
                    {"conditions": {"combinator": "and", "conditions": [
                        {"leftValue": "={{ $('Webhook - Entrante').item.json.body.MessageType }}",
                         "rightValue": "audio",
                         "operator": {"type": "string", "operation": "equals"}}
                    ]}, "outputKey": "Audio"},
                    {"conditions": {"combinator": "and", "conditions": [
                        {"leftValue": "={{ $('Webhook - Entrante').item.json.body.MessageType }}",
                         "rightValue": "image",
                         "operator": {"type": "string", "operation": "equals"}}
                    ]}, "outputKey": "Image"},
                ]},
            }},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    # Nivel superior: n8n lo expone como $json.body.MessageType == "text".
    assert env["MessageType"] == "text"
    assert "body" not in env
    # y el mensaje sigue llegando (garantia de texto siempre presente)
    assert MARCADOR_MENSAJE in json.dumps(env)


def test_switch_optional_chaining_y_string_wrap():
    """Caso real de producción: el Switch lee el campo con optional chaining y
    envuelto en String(...).toLowerCase(). El extractor debe hallar body.MessageType
    a pesar del `?.`; sin esto el sobre no lleva MessageType y el flujo se atasca
    en el Switch (3/95 nodos, 'No output data in this branch')."""
    wf = {
        "name": "RouterProd",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.switch", "name": "Route Types1", "parameters": {
                "mode": "rules",
                "rules": {"values": [
                    {"conditions": {"combinator": "and", "conditions": [
                        {"leftValue": "={{ String($json.body?.MessageType || '').toLowerCase() }}",
                         "rightValue": "text",
                         "operator": {"type": "string", "operation": "equals"}}
                    ]}, "outputKey": "Text"},
                    {"conditions": {"combinator": "and", "conditions": [
                        {"leftValue": "={{ String($json.body?.MessageType || '').toLowerCase() }}",
                         "rightValue": "audio",
                         "operator": {"type": "string", "operation": "equals"}}
                    ]}, "outputKey": "Audio"},
                ]},
            }},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert env["MessageType"] == "text"
    assert "body" not in env


def test_path_desde_expresion_con_optional_chaining():
    """El helper de rutas tolera `?.` en cualquier posición."""
    assert _m._path_desde_expresion_n8n("={{ $json.body?.MessageType }}") == ["body", "MessageType"]
    assert _m._path_desde_expresion_n8n("={{ $json?.body?.type }}") == ["body", "type"]
    assert _m._path_desde_expresion_n8n("={{ String($json.body?.a?.b || '') }}") == ["body", "a", "b"]


def test_path_descarta_llamadas_de_metodo_js():
    """Un método JS tras el campo (`.replace()`, `.toLowerCase()`) NO es un campo:
    debe descartarse para no fabricar basura (ej. message -> {replace: ...})."""
    assert _m._path_desde_expresion_n8n("={{ $json.message.replace(/'/g, \"''\") }}") == ["message"]
    assert _m._path_desde_expresion_n8n("={{ $json.body.tipo.toLowerCase() }}") == ["body", "tipo"]
    assert _m._path_desde_expresion_n8n("={{ $json.texto.trim() }}") == ["texto"]


def test_envelope_message_es_string_no_objeto():
    """Con un flujo que usa $json.message.replace(...), el sobre debe dejar
    `message` como STRING (marcador), nunca como objeto {replace: ...}."""
    wf = {
        "name": "Buffer",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.postgres", "name": "Subir a memoria", "parameters": {
                "query": "INSERT INTO buffer (content) VALUES ('{{ $json.message.replace(/'/g, \"''\") }}')"
            }},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert isinstance(env.get("message"), str)
    assert "replace" not in env


def test_if_v2_condicion_igualdad_fija_campo():
    """Un nodo IF por igualdad tambien fija el campo a la rama true."""
    wf = {
        "name": "Gate",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.if", "name": "Es WhatsApp", "parameters": {
                "conditions": {"combinator": "and", "conditions": [
                    {"leftValue": "={{ $json.body.channel }}",
                     "rightValue": "whatsapp",
                     "operator": {"type": "string", "operation": "equals"}}
                ]}},
            },
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert env["channel"] == "whatsapp"


def test_switch_legacy_value1_rules():
    """Switch legacy (value1 + rules.rules[].value2) tambien se infiere."""
    wf = {
        "name": "LegacyRouter",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.switch", "name": "Route", "parameters": {
                "value1": "={{ $json.body.type }}",
                "rules": {"rules": [
                    {"value2": "text", "output": 0},
                    {"value2": "audio", "output": 1},
                ]},
            }},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert env["type"] == "text"


def test_routing_no_pisa_marcador_de_mensaje():
    """Si el Switch enruta sobre el propio campo de mensaje, no se pierde el
    texto real: el marcador manda sobre el literal de la rama."""
    wf = {
        "name": "R",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.if", "name": "IF", "parameters": {
                "conditions": {"combinator": "and", "conditions": [
                    {"leftValue": "={{ $json.body.message }}",
                     "rightValue": "hola",
                     "operator": {"type": "string", "operation": "equals"}}
                ]}},
            },
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    assert env["message"] == MARCADOR_MENSAJE


def test_solo_despoja_el_body_envoltorio_no_la_estructura_interna():
    """WhatsApp anidado: se quita el `body` que n8n añade, pero se conserva la
    estructura real del sender (messages[0]... via campos intermedios)."""
    wf = {
        "name": "WA-nested",
        "nodes": [
            {"type": "n8n-nodes-base.webhook", "name": "Webhook", "parameters": {}},
            {"type": "n8n-nodes-base.set", "name": "X", "parameters": {
                "assignments": {"assignments": [
                    {"name": "t", "value": "={{ $json.body.contacts.wa_id }}"},
                ]}}},
        ],
        "connections": {},
    }
    env = _m.inferir_envelope_desde_wf(wf)
    # `body` (envoltorio n8n) se quita; `contacts` (estructura del sender) queda.
    assert "body" not in env
    assert env["contacts"]["wa_id"] == MARCADOR_SESSION


def test_payload_template_explicito_ignora_envelope_hint():
    """Si el usuario da un payload_template, manda ese; el hint no interfiere."""
    tmpl = {"custom": {"txt": MARCADOR_MENSAJE}}
    a = N8nAdapter(webhook_url="http://x", payload_template=tmpl,
                   envelope_hint={"body": {"message": MARCADOR_MENSAJE}})
    # send_message usa payload_template tal cual (via _sustituir_marcadores);
    # el envelope_hint solo aplica en el payload generico (_build_payload).
    assert a.payload_template == tmpl
