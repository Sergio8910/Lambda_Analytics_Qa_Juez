"""Tests de DriveSource. NO toca Drive real — todo mockeado."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
import requests

from verificador.sources.drive import DriveSource
from verificador.sources.base import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceTimeoutError,
)


def _mock_response(status_code: int, content: bytes = b"") -> MagicMock:
    """Construye un mock de requests.Response que se comporta como context manager."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    if 200 <= status_code < 300:
        # iter_content devuelve el blob en un solo chunk
        resp.iter_content = MagicMock(return_value=iter([content]))
        resp.raise_for_status = MagicMock(return_value=None)
    else:
        def _raise():
            raise requests.exceptions.HTTPError(f"HTTP {status_code}")
        resp.raise_for_status = MagicMock(side_effect=_raise)
        resp.iter_content = MagicMock(return_value=iter([]))
    return resp


def test_descarga_ok_devuelve_bytes():
    expected = b"%PDF-1.4 fake pdf content"
    with patch("verificador.sources.drive.requests.get", return_value=_mock_response(200, expected)) as mocked:
        src = DriveSource(token="fake-token", retry_max=1)
        result = src.fetch({"file_id": "fake_id"})
    assert result == expected
    # Verificar que pasó el header de auth (sin printear el token)
    _, kwargs = mocked.call_args
    assert "Authorization" in kwargs["headers"]
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")


def test_404_levanta_source_not_found_sin_retry():
    with patch("verificador.sources.drive.requests.get", return_value=_mock_response(404)) as mocked:
        src = DriveSource(token="fake", retry_max=3, retry_base_delay_s=0)
        with pytest.raises(SourceNotFoundError):
            src.fetch({"file_id": "nope"})
    # Verifica que NO reintentó (404 es definitivo)
    assert mocked.call_count == 1


def test_401_levanta_source_auth_error_sin_retry():
    with patch("verificador.sources.drive.requests.get", return_value=_mock_response(401)) as mocked:
        src = DriveSource(token="fake", retry_max=3, retry_base_delay_s=0)
        with pytest.raises(SourceAuthError):
            src.fetch({"file_id": "x"})
    assert mocked.call_count == 1


def test_403_levanta_source_auth_error():
    with patch("verificador.sources.drive.requests.get", return_value=_mock_response(403)):
        with pytest.raises(SourceAuthError):
            DriveSource(token="x", retry_max=1).fetch({"file_id": "x"})


def test_500_reintenta_y_agota():
    """5xx es transient — debe reintentar hasta retry_max y luego SourceTimeoutError."""
    with patch("verificador.sources.drive.requests.get", return_value=_mock_response(503)) as mocked:
        src = DriveSource(token="x", retry_max=3, retry_base_delay_s=0)
        with pytest.raises(SourceTimeoutError):
            src.fetch({"file_id": "x"})
    assert mocked.call_count == 3


def test_500_luego_200_eventual_consistency():
    """Caso típico: Drive recién subió, el primer GET 503 pero el segundo OK."""
    responses = [_mock_response(503), _mock_response(200, b"%PDF-OK")]
    with patch("verificador.sources.drive.requests.get", side_effect=responses) as mocked:
        src = DriveSource(token="x", retry_max=3, retry_base_delay_s=0)
        blob = src.fetch({"file_id": "x"})
    assert blob == b"%PDF-OK"
    assert mocked.call_count == 2


def test_timeout_reintenta():
    """Timeout transient — reintenta."""
    side = [requests.exceptions.Timeout("t1"), _mock_response(200, b"OK")]
    with patch("verificador.sources.drive.requests.get", side_effect=side) as mocked:
        src = DriveSource(token="x", retry_max=3, retry_base_delay_s=0)
        result = src.fetch({"file_id": "x"})
    assert result == b"OK"
    assert mocked.call_count == 2


def test_falta_token_levanta_auth_error():
    src = DriveSource(token=None, retry_max=1)
    with pytest.raises(SourceAuthError):
        src.fetch({"file_id": "x"})


def test_falta_file_id_levanta_source_error():
    src = DriveSource(token="x", retry_max=1)
    with pytest.raises(SourceError):
        src.fetch({})


def test_blob_grande_excede_cap():
    """Si el blob excede max_bytes, debe abortarse antes de cargarlo todo."""
    chunk = b"x" * 1024  # 1KB
    resp = MagicMock()
    resp.status_code = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.iter_content = MagicMock(return_value=iter([chunk] * 200))  # 200KB total
    resp.raise_for_status = MagicMock(return_value=None)
    with patch("verificador.sources.drive.requests.get", return_value=resp):
        src = DriveSource(token="x", retry_max=1, max_bytes=10 * 1024)  # 10KB cap
        with pytest.raises(SourceTimeoutError):
            # Lo envuelve como SourceTimeoutError porque agota retries (no es 404/401)
            src.fetch({"file_id": "x"})


def test_source_registrada():
    from verificador.sources import get_source

    src = get_source("drive")
    assert src.type_name == "drive"
