"""Tests del modo sintético de QA de PDF (sin disparar el flujo real).

Genera el PDF con build_synthetic_pdf vía el driver 'synthetic_pdf' y lo evalúa
con el evaluador 'synthetic_pdf'. Cero red, cero webhook, cero BD.
"""
from __future__ import annotations

import base64

import pytest

from juez.evaluation.artifact.registry import make_driver, make_evaluator
from juez.evaluation.artifact.run import run_artifact_eval


def test_driver_genera_pdf_sin_disparar():
    pytest.importorskip("reportlab")
    drv = make_driver("synthetic_pdf", batch_id="test-batch", plan_idx=1)
    tr = drv.trigger({})
    assert tr["ok"] is True
    assert tr["synthetic"] is True
    assert tr["http_status"] is None  # no hubo llamada HTTP
    assert tr["response"]["pdf_base64"]
    # el PDF decodifica y empieza con la firma %PDF
    blob = base64.b64decode(tr["response"]["pdf_base64"])
    assert blob[:5] == b"%PDF-"
    assert "counts" in tr["expected_snapshot"]


def test_driver_determinista():
    pytest.importorskip("reportlab")
    a = make_driver("synthetic_pdf", batch_id="fijo", plan_idx=1).trigger({})
    b = make_driver("synthetic_pdf", batch_id="fijo", plan_idx=1).trigger({})
    assert a["expected_snapshot"] == b["expected_snapshot"]


def test_evaluador_verifica_pdf_consistente():
    pytest.importorskip("reportlab")
    drv = make_driver("synthetic_pdf", batch_id="eval-batch", plan_idx=1)
    tr = drv.trigger({})
    ev = make_evaluator("synthetic_pdf")
    res = ev.evaluate({"trigger_result": tr, "expected_snapshot": tr["expected_snapshot"]})
    # PDF y snapshot son consistentes -> debe salir bien
    assert res["score"] >= 99.0
    assert res["metricas"]["contenido_verificado"] is True
    assert res["metricas"]["fotos_embebidas"] == res["metricas"]["fotos_esperadas"]
    assert not res["metricas"].get("ambientes_faltantes")


def test_evaluador_detecta_pdf_invalido():
    ev = make_evaluator("synthetic_pdf")
    res = ev.evaluate({"trigger_result": {"ok": False, "error": "boom", "response": {}}})
    assert res["score"] == 0.0
    assert any(p["severidad"] == "CRITICO" for p in res["problemas"])


def test_run_artifact_eval_sintetico_e2e():
    pytest.importorskip("reportlab")
    r = run_artifact_eval("mvp_abad_telegram")
    assert r, "el spec debe existir y correr"
    assert r["score_artefacto"] >= 99.0
    assert len(r["problemas"]) == 0
    m = r["por_evaluador"][0]["metricas"]
    assert m["contenido_verificado"] is True
    assert m["paginas"] >= 1
    assert "QA DE ARTEFACTO" in r["reporte"]
