"""Test del generador de escenarios (parser, sin red)."""
from __future__ import annotations

from juez.evaluation.autogen.scenario_gen import _build_user, _parse


def test_parse_escenarios():
    raw = '{"escenarios":[{"titulo":"A","tipo":"happy_path","descripcion":"x"},{"titulo":"B","tipo":"edge"}]}'
    out = _parse(raw)
    assert len(out) == 2
    assert out[0]["tipo"] == "happy_path"


def test_parse_vacio_o_invalido():
    assert _parse("{}") == []
    assert _parse("") == []


def test_build_user_incluye_contexto():
    msg = _build_user("banca", "soporte tarjetas", "resolver dudas", "llamada", 5)
    assert "banca" in msg and "soporte tarjetas" in msg and "llamada" in msg and "5" in msg
