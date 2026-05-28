from __future__ import annotations

from evaluation.scorecard.anti_gaming import evaluate_anti_gaming
from evaluation.report_models import TaskContract


def test_anti_gaming_repetitive_disclaimer():
    output = (
        "La siguiente respuesta está redactada en español. Según el contexto, esto es válido. "
        "Según el contexto, esto se repite."
    )
    res = evaluate_anti_gaming(output, TaskContract())
    codes = [f.code for f in res.flags]
    assert "repetitive_disclaimer" in codes
