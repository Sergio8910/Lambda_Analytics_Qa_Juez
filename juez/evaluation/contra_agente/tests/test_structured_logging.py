"""Tests para juez.utils.structured_logging."""
from __future__ import annotations

import json
import logging

import pytest

from juez.utils.structured_logging import setup_logging


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Snapshot/restore del root logger entre tests."""
    root = logging.getLogger()
    prev_handlers = list(root.handlers)
    prev_level = root.level
    yield
    root.handlers = prev_handlers
    root.setLevel(prev_level)


def _emit(message: str, *, extra: dict | None = None, level: str = "info") -> None:
    """Emite un log y fuerza flush de los handlers."""
    log = logging.getLogger("juez.test.structured_logging")
    log.propagate = True
    getattr(log, level)(message, extra=extra or {})
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass


def test_format_text_default(monkeypatch, capsys):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    setup_logging(level="INFO")
    _emit("hola mundo")
    err = capsys.readouterr().err
    assert "hola mundo" in err
    # No debe ser JSON parseable
    line = err.strip().splitlines()[-1]
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_format_json_via_env(monkeypatch, capsys):
    monkeypatch.setenv("LOG_FORMAT", "json")
    setup_logging(level="INFO")
    _emit("mensaje-json")
    err = capsys.readouterr().err
    line = err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["msg"] == "mensaje-json"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "juez.test.structured_logging"
    assert "ts" in payload


def test_redacta_password_en_texto(monkeypatch, capsys):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    setup_logging(level="INFO")
    _emit("user=foo password=secret123 other=ok")
    err = capsys.readouterr().err
    assert "secret123" not in err
    assert "***REDACTED***" in err


def test_redacta_authorization_header(monkeypatch, capsys):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    setup_logging(level="INFO")
    _emit("Authorization: Bearer xyz123abc")
    err = capsys.readouterr().err
    assert "xyz123abc" not in err
    assert "***REDACTED***" in err


def test_extra_fields_aparecen_en_json(monkeypatch, capsys):
    monkeypatch.setenv("LOG_FORMAT", "json")
    setup_logging(level="INFO")
    _emit("with extra", extra={"req_id": "abc"})
    err = capsys.readouterr().err
    line = err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["req_id"] == "abc"
    assert payload["msg"] == "with extra"


def test_setup_logging_explicit_fmt_gana_sobre_env(monkeypatch, capsys):
    monkeypatch.setenv("LOG_FORMAT", "json")
    setup_logging(level="INFO", fmt="text")
    _emit("explicit-wins")
    err = capsys.readouterr().err
    line = err.strip().splitlines()[-1]
    assert "explicit-wins" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
