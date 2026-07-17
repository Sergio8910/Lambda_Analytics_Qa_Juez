"""Reintentos del N8nAdapter ante fallos transitorios (timeout/conexion/5xx),
sin reintentar 4xx. Un blip pasajero en un turno no debe envenenar la
conversacion entera.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

import juez.evaluation.contra_agente.adapters.n8n as n8n_mod
from juez.evaluation.contra_agente.adapters.n8n import N8nAdapter


@pytest.fixture(autouse=True)
def _sin_sleep(monkeypatch):
    # No dormir en tests: backoff a 0.
    monkeypatch.setattr(n8n_mod, "_BACKOFF_BASE_S", 0.0)


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


def test_reintenta_5xx_y_luego_tiene_exito():
    adapter = N8nAdapter("https://x/webhook")
    respuestas = [_resp(503), _resp(500), _resp(200, {"output": "hola"})]
    with patch.object(n8n_mod.requests, "post", side_effect=respuestas) as mock_post:
        texto, _ = adapter.send_message("hola", [])
    assert mock_post.call_count == 3
    assert texto == "hola"


def test_5xx_persistente_agota_intentos_y_reporta_error():
    adapter = N8nAdapter("https://x/webhook")
    with patch.object(n8n_mod.requests, "post", return_value=_resp(500)) as mock_post:
        texto, _ = adapter.send_message("hola", [])
    assert mock_post.call_count == n8n_mod._MAX_INTENTOS
    assert texto.startswith("[ERROR")
    assert adapter.last_debug.get("status_code") == 500


def test_timeout_persistente_reintenta_y_reporta():
    adapter = N8nAdapter("https://x/webhook")
    with patch.object(n8n_mod.requests, "post", side_effect=requests.exceptions.Timeout("t")) as mock_post:
        texto, _ = adapter.send_message("hola", [])
    assert mock_post.call_count == n8n_mod._MAX_INTENTOS
    assert texto.startswith("[ERROR")
    assert adapter.last_debug.get("error")


def test_4xx_no_se_reintenta():
    adapter = N8nAdapter("https://x/webhook")
    with patch.object(n8n_mod.requests, "post", return_value=_resp(404)) as mock_post:
        texto, _ = adapter.send_message("hola", [])
    assert mock_post.call_count == 1  # 4xx no se reintenta
    assert texto.startswith("[ERROR")


def test_exito_al_primer_intento_no_reintenta():
    adapter = N8nAdapter("https://x/webhook")
    with patch.object(n8n_mod.requests, "post", return_value=_resp(200, {"output": "ok"})) as mock_post:
        texto, _ = adapter.send_message("hola", [])
    assert mock_post.call_count == 1
    assert texto == "ok"
