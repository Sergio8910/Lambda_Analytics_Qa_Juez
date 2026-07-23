"""OrdoAdapter: flujo asincrono por cuenta (POST 202 -> polling GET hasta
done=true), reuso de conversation_id entre turnos, y fallos de transporte
compatibles con la deteccion del worker (last_debug).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

import juez.evaluation.contra_agente.adapters.ordo as ordo_mod
from juez.evaluation.contra_agente.adapters.ordo import OrdoAdapter


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    # No dormir en tests: backoff del POST y polling a 0.
    monkeypatch.setattr(ordo_mod, "_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(ordo_mod.time, "sleep", lambda *_: None)


def _resp(status_code: int, body: dict | None = None):
    class _R:
        def __init__(self):
            self.status_code = status_code
            self.text = "{}"

        def json(self):
            return body or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(f"{self.status_code} error")

    return _R()


def test_flujo_feliz_post_luego_polling():
    adapter = OrdoAdapter(api_key="k", server="server-aws-3")
    post = _resp(202, {"conversation_id": "abc", "status": "running"})
    gets = [
        _resp(200, {"status": "running", "done": False}),
        _resp(200, {"status": "success", "done": True, "reply": "el server esta OK"}),
    ]
    with patch.object(ordo_mod.requests, "post", return_value=post) as mp, \
         patch.object(ordo_mod.requests, "get", side_effect=gets) as mg:
        texto, _ = adapter.send_message("estado?", [])
    assert texto == "el server esta OK"
    assert adapter.conversation_id == "abc"
    assert mp.call_count == 1
    assert mg.call_count == 2  # siguio en running y luego termino
    assert adapter.last_debug.get("status_code") == 200


def test_primer_post_manda_server_y_reusa_conversation_id():
    adapter = OrdoAdapter(api_key="k", server="server-aws-3", project="infra")
    post = _resp(202, {"conversation_id": "cid", "status": "running"})
    done = _resp(200, {"done": True, "reply": "ok"})
    with patch.object(ordo_mod.requests, "post", return_value=post) as mp, \
         patch.object(ordo_mod.requests, "get", return_value=done):
        adapter.send_message("turno 1", [])
        adapter.send_message("turno 2", [])

    primer_body = mp.call_args_list[0].kwargs["json"]
    segundo_body = mp.call_args_list[1].kwargs["json"]
    assert primer_body["server"] == "server-aws-3"
    assert primer_body["project"] == "infra"
    assert "conversation_id" not in primer_body  # primer turno: sin id
    # segundo turno: reenvia el id y ya NO reenvia server/project
    assert segundo_body["conversation_id"] == "cid"
    assert "server" not in segundo_body


def test_header_api_key_va_en_x_api_key():
    adapter = OrdoAdapter(api_key="ordo_sk_test")
    post = _resp(202, {"conversation_id": "z"})
    done = _resp(200, {"done": True, "reply": "r"})
    with patch.object(ordo_mod.requests, "post", return_value=post) as mp, \
         patch.object(ordo_mod.requests, "get", return_value=done):
        adapter.send_message("hola", [])
    assert mp.call_args.kwargs["headers"]["X-Api-Key"] == "ordo_sk_test"


def test_api_key_desde_env(monkeypatch):
    monkeypatch.setenv("ORDO_API_KEY", "desde_env")
    assert OrdoAdapter().api_key == "desde_env"


def test_post_5xx_reintenta_y_luego_exito():
    adapter = OrdoAdapter(api_key="k")
    posts = [_resp(503), _resp(202, {"conversation_id": "c"})]
    done = _resp(200, {"done": True, "reply": "ok"})
    with patch.object(ordo_mod.requests, "post", side_effect=posts) as mp, \
         patch.object(ordo_mod.requests, "get", return_value=done):
        texto, _ = adapter.send_message("hola", [])
    assert mp.call_count == 2
    assert texto == "ok"


def test_post_4xx_es_fallo_de_transporte_sin_reintento():
    adapter = OrdoAdapter(api_key="k")
    with patch.object(ordo_mod.requests, "post", return_value=_resp(401)) as mp:
        texto, _ = adapter.send_message("hola", [])
    assert mp.call_count == 1
    assert texto.startswith("[ERROR")
    assert adapter.last_debug.get("error")  # el worker lo trata como fallo de transporte


def test_estado_error_en_polling_es_fallo():
    adapter = OrdoAdapter(api_key="k")
    post = _resp(202, {"conversation_id": "c"})
    fail = _resp(200, {"status": "error", "done": False, "error": "el agente exploto"})
    with patch.object(ordo_mod.requests, "post", return_value=post), \
         patch.object(ordo_mod.requests, "get", return_value=fail):
        texto, _ = adapter.send_message("hola", [])
    assert texto.startswith("[ERROR")
    assert "error" in adapter.last_debug.get("error", "")


def test_polling_timeout_es_fallo():
    adapter = OrdoAdapter(api_key="k", poll_timeout_s=0.0)
    post = _resp(202, {"conversation_id": "c"})
    running = _resp(200, {"status": "running", "done": False})
    with patch.object(ordo_mod.requests, "post", return_value=post), \
         patch.object(ordo_mod.requests, "get", return_value=running):
        texto, _ = adapter.send_message("hola", [])
    assert texto.startswith("[ERROR")
    assert "timeout" in adapter.last_debug.get("error", "").lower()
