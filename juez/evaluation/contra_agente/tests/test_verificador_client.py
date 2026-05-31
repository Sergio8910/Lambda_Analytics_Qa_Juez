"""Tests unitarios para verificador_client (sin red).

Mockeamos `juez.evaluation.contra_agente.verificador_client.requests` completo,
encajamos secuencias de respuestas con side_effect y verificamos:
  - healthcheck() devuelve True/False según status y errores.
  - verify_inline_pdf() happy path con polling running→completed.
  - verify_inline_pdf() levanta VerificadorUnavailable en 5xx, timeout de polling
    y respuestas no-JSON.
  - El header X-Verifier-Key se incluye cuando settings.VERIFICADOR_API_KEY está set.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests_real

from juez.evaluation.contra_agente import verificador_client as vc
from juez.evaluation.contra_agente.verificador_client import (
    VerificadorUnavailable,
    healthcheck,
    verify_inline_pdf,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def make_resp(status, body=None):
    r = MagicMock()
    r.status_code = status
    r.text = json.dumps(body or {})
    r.json.return_value = body if body is not None else {}
    r.raise_for_status = MagicMock()
    return r


def _kwargs_for_verify(**overrides):
    base = dict(
        cliente="abad_synthetic",
        artifact_id="art_test_1",
        pdf_bytes=b"%PDF-fake-bytes",
        expected_snapshot={"items": [{"sku": "A1", "qty": 3}]},
        base_url="http://verificador.test",
        poll_timeout_s=2.0,
        poll_interval_s=0.0,
    )
    base.update(overrides)
    return base


# ── healthcheck ──────────────────────────────────────────────────────────────
def test_healthcheck_ok_returns_true():
    with patch.object(vc, "requests") as mock_req:
        mock_req.get.return_value = make_resp(200, {"ok": True})
        assert healthcheck(base_url="http://verificador.test", timeout_s=1.0) is True
        called_url = mock_req.get.call_args[0][0]
        assert called_url == "http://verificador.test/health"


def test_healthcheck_non_200_returns_false():
    with patch.object(vc, "requests") as mock_req:
        mock_req.get.return_value = make_resp(503, {"err": "down"})
        assert healthcheck(base_url="http://verificador.test", timeout_s=1.0) is False


def test_healthcheck_timeout_returns_false():
    with patch.object(vc, "requests") as mock_req:
        # Exponer la excepción real del módulo requests para que el except la atrape.
        mock_req.RequestException = _requests_real.RequestException
        mock_req.get.side_effect = _requests_real.Timeout("boom")
        assert healthcheck(base_url="http://verificador.test", timeout_s=0.1) is False


def test_healthcheck_generic_exception_returns_false():
    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.get.side_effect = RuntimeError("connection refused")
        assert healthcheck(base_url="http://verificador.test", timeout_s=0.1) is False


# ── verify_inline_pdf — happy path ───────────────────────────────────────────
def test_verify_inline_pdf_happy_path_polling_running_then_completed():
    dispatch = make_resp(
        202,
        {
            "verification_id": "verif_abc",
            "status": "queued",
            "poll_url": "/verificador/verify/verif_abc",
        },
    )
    poll_running = make_resp("running_dummy_status", {"status": "running"})
    poll_running.status_code = 200
    poll_done = make_resp(
        200,
        {
            "status": "completed",
            "verdict": "OK",
            "score": 0.95,
            "checks": [{"name": "items_match", "ok": True}],
            "issues": [],
        },
    )

    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = dispatch
        mock_req.get.side_effect = [poll_running, poll_done]

        result = verify_inline_pdf(**_kwargs_for_verify())

        assert result["status"] == "completed"
        assert result["verdict"] == "OK"
        assert result["score"] == 0.95
        # POST llamado al endpoint esperado.
        post_url = mock_req.post.call_args[0][0]
        assert post_url == "http://verificador.test/verificador/verify"
        # Body lleva base64 inline + metadata.expected_snapshot.
        kwargs = mock_req.post.call_args.kwargs
        body = kwargs["json"]
        assert body["cliente"] == "abad_synthetic"
        assert body["artifact_type"] == "pdf"
        assert body["source"]["type"] == "inline"
        assert "blob_base64" in body["source"]
        assert body["metadata"]["synthetic"] is True
        assert body["metadata"]["expected_snapshot"] == {
            "items": [{"sku": "A1", "qty": 3}]
        }
        # Polling: 2 GETs realizados.
        assert mock_req.get.call_count == 2
        get_url = mock_req.get.call_args_list[0][0][0]
        assert get_url == "http://verificador.test/verificador/verify/verif_abc"


# ── verify_inline_pdf — error 5xx en dispatch ────────────────────────────────
def test_verify_inline_pdf_dispatch_5xx_raises_unavailable():
    bad = make_resp(503, {"error": "upstream"})
    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = bad

        with pytest.raises(VerificadorUnavailable):
            verify_inline_pdf(**_kwargs_for_verify())

        # No debe haber pasado a polling.
        mock_req.get.assert_not_called()


def test_verify_inline_pdf_dispatch_500_raises_unavailable():
    bad = make_resp(500, {"error": "boom"})
    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = bad
        with pytest.raises(VerificadorUnavailable):
            verify_inline_pdf(**_kwargs_for_verify())


# ── verify_inline_pdf — polling timeout ──────────────────────────────────────
def test_verify_inline_pdf_polling_timeout_raises_unavailable():
    dispatch = make_resp(
        202, {"verification_id": "verif_slow", "status": "queued"}
    )
    running = make_resp(200, {"status": "running"})

    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = dispatch
        # Siempre running → forzamos timeout. side_effect infinito vía función.
        mock_req.get.side_effect = lambda *a, **kw: running

        with pytest.raises(VerificadorUnavailable) as ei:
            verify_inline_pdf(
                **_kwargs_for_verify(poll_timeout_s=0.05, poll_interval_s=0.0)
            )
        assert "polling timeout" in str(ei.value)


# ── verify_inline_pdf — respuesta no-JSON ────────────────────────────────────
def test_verify_inline_pdf_dispatch_non_json_raises_unavailable():
    bad = MagicMock()
    bad.status_code = 202
    bad.text = "<html>not json</html>"
    bad.json.side_effect = ValueError("no json")

    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = bad

        with pytest.raises(VerificadorUnavailable):
            verify_inline_pdf(**_kwargs_for_verify())


def test_verify_inline_pdf_poll_non_json_raises_unavailable():
    dispatch = make_resp(202, {"verification_id": "verif_xyz", "status": "queued"})
    bad_poll = MagicMock()
    bad_poll.status_code = 200
    bad_poll.text = "garbage"
    bad_poll.json.side_effect = ValueError("nope")

    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = dispatch
        mock_req.get.return_value = bad_poll

        with pytest.raises(VerificadorUnavailable):
            verify_inline_pdf(**_kwargs_for_verify())


# ── verify_inline_pdf — sin verification_id en respuesta ─────────────────────
def test_verify_inline_pdf_missing_verification_id_raises_unavailable():
    dispatch = make_resp(202, {"status": "queued"})  # falta verification_id
    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.return_value = dispatch
        with pytest.raises(VerificadorUnavailable):
            verify_inline_pdf(**_kwargs_for_verify())


# ── verify_inline_pdf — header X-Verifier-Key ────────────────────────────────
def test_verify_inline_pdf_sends_api_key_header_when_set():
    dispatch = make_resp(202, {"verification_id": "verif_k", "status": "queued"})
    poll_done = make_resp(
        200, {"status": "completed", "verdict": "OK", "score": 1.0, "checks": []}
    )

    with patch.object(vc.settings, "VERIFICADOR_API_KEY", "secret-key-123"):
        with patch.object(vc, "requests") as mock_req:
            mock_req.RequestException = _requests_real.RequestException
            mock_req.post.return_value = dispatch
            mock_req.get.return_value = poll_done

            verify_inline_pdf(**_kwargs_for_verify())

            post_headers = mock_req.post.call_args.kwargs["headers"]
            assert post_headers.get("X-Verifier-Key") == "secret-key-123"
            assert post_headers.get("Content-Type") == "application/json"

            get_headers = mock_req.get.call_args.kwargs["headers"]
            assert get_headers.get("X-Verifier-Key") == "secret-key-123"


def test_verify_inline_pdf_no_api_key_header_when_unset():
    dispatch = make_resp(202, {"verification_id": "verif_n", "status": "queued"})
    poll_done = make_resp(
        200, {"status": "completed", "verdict": "OK", "score": 1.0, "checks": []}
    )

    with patch.object(vc.settings, "VERIFICADOR_API_KEY", None):
        with patch.object(vc, "requests") as mock_req:
            mock_req.RequestException = _requests_real.RequestException
            mock_req.post.return_value = dispatch
            mock_req.get.return_value = poll_done

            verify_inline_pdf(**_kwargs_for_verify())

            post_headers = mock_req.post.call_args.kwargs["headers"]
            assert "X-Verifier-Key" not in post_headers


# ── verify_inline_pdf — RequestException en dispatch ─────────────────────────
def test_verify_inline_pdf_dispatch_connection_error_raises_unavailable():
    with patch.object(vc, "requests") as mock_req:
        mock_req.RequestException = _requests_real.RequestException
        mock_req.post.side_effect = _requests_real.ConnectionError("refused")

        with pytest.raises(VerificadorUnavailable):
            verify_inline_pdf(**_kwargs_for_verify())
