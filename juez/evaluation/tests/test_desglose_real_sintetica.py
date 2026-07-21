"""Combinación de pruebas reales + sintéticas con conteo de cada una.
Antes modo_ejecucion era either/or y no había desglose; ahora se puede correr
N reales + M sintéticas en la misma evaluación y el reporte dice cuántas de cada.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from juez.api.runner import _desglose_ejecucion, run_n8n_single


def _batch(total, passed, failed, pass_rate):
    return SimpleNamespace(
        total=total, passed=passed, failed=failed, pass_rate=pass_rate,
        by_category={}, results=[],
    )


def test_desglose_cuenta_reales_y_sinteticas():
    d = _desglose_ejecucion(_batch(3, 2, 1, 0.66), _batch(5, 5, 0, 1.0))
    assert d["total_reales"] == 3
    assert d["total_sinteticas"] == 5
    assert d["total_combinado"] == 8
    assert d["reales"]["aprobadas"] == 2
    assert d["sinteticas"]["aprobadas"] == 5
    assert d["reales"]["ejecutadas"] is True
    assert d["sinteticas"]["ejecutadas"] is True


def test_desglose_solo_reales():
    d = _desglose_ejecucion(_batch(4, 4, 0, 1.0), None)
    assert d["total_reales"] == 4
    assert d["total_sinteticas"] == 0
    assert d["sinteticas"]["ejecutadas"] is False


def test_desglose_solo_sinteticas():
    d = _desglose_ejecucion(None, _batch(6, 3, 3, 0.5))
    assert d["total_reales"] == 0
    assert d["total_sinteticas"] == 6
    assert d["reales"]["ejecutadas"] is False


def test_desglose_ninguna():
    d = _desglose_ejecucion(None, None)
    assert d["total_combinado"] == 0
    assert d["reales"]["ejecutadas"] is False and d["sinteticas"]["ejecutadas"] is False


def test_run_n8n_single_expone_conteos_reales_y_sinteticas():
    """El wiring: run_n8n_single acepta cuántas reales y cuántas sintéticas."""
    params = inspect.signature(run_n8n_single).parameters
    assert "conversaciones_reales" in params
    assert "conversaciones_sinteticas" in params
