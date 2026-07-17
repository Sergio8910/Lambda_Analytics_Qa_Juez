"""El healthcheck del webhook no debe reportar 'activo' cuando el host es
inalcanzable -- antes devolvia True ante timeout/ConnectionError y el caller
gastaba generacion GPT + N conversaciones contra un webhook muerto.
"""
from __future__ import annotations

import requests

from juez.evaluar_pipeline import _verificar_webhook_activo


def test_timeout_reporta_inactivo(monkeypatch):
    def _boom(*a, **k):
        raise requests.exceptions.Timeout("probe timed out")
    monkeypatch.setattr(requests, "post", _boom)
    activo, msg = _verificar_webhook_activo("https://n8n.example.com/webhook/x")
    assert activo is False
    assert "inalcanzable" in msg.lower()


def test_connection_error_reporta_inactivo(monkeypatch):
    def _boom(*a, **k):
        raise requests.exceptions.ConnectionError("name resolution failed")
    monkeypatch.setattr(requests, "post", _boom)
    activo, msg = _verificar_webhook_activo("https://n8n.example.com/webhook/x")
    assert activo is False


def test_error_inesperado_sigue_siendo_indulgente(monkeypatch):
    """Un error NO de conectividad (bug del probe) mantiene el comportamiento
    indulgente de intentar igual (activo=True)."""
    def _boom(*a, **k):
        raise ValueError("algo raro en el probe, no de red")
    monkeypatch.setattr(requests, "post", _boom)
    activo, _ = _verificar_webhook_activo("https://n8n.example.com/webhook/x")
    assert activo is True


def test_respuesta_200_es_activo(monkeypatch):
    class _Resp:
        status_code = 200
        def json(self):
            return {}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    activo, _ = _verificar_webhook_activo("https://n8n.example.com/webhook/x")
    assert activo is True
