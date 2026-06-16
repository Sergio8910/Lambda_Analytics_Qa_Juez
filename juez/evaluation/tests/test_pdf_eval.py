"""Tests de la evaluación autónoma de PDF (sin BD/Drive/spec)."""
from __future__ import annotations

import pytest

from juez.evaluation.artifact.pdf_eval import evaluate_pdf


def _pdf_con_texto(texto: str, *, con_imagen: bool = False) -> bytes:
    """Genera un PDF real en memoria con PyMuPDF."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), texto)
    if con_imagen:
        # Genera un PNG real con Pillow y lo embebe como imagen.
        Image = pytest.importorskip("PIL.Image", reason="Pillow requerido")
        import io as _io
        from PIL import Image as _Image

        buf = _io.BytesIO()
        _Image.new("RGB", (32, 32), (200, 30, 30)).save(buf, format="PNG")
        page.insert_image(fitz.Rect(72, 100, 172, 200), stream=buf.getvalue())
    out = doc.tobytes()
    doc.close()
    return out


def test_pdf_integro_sin_expectativas_es_ok():
    pdf = _pdf_con_texto("Informe de inventario")
    res = evaluate_pdf(pdf)
    assert res["veredicto"] == "OK"
    assert res["score_global"] == 100.0
    assert res["metricas"]["paginas"] == 1


def test_pdf_corrupto_es_unverifiable():
    res = evaluate_pdf(b"esto no es un pdf")
    assert res["veredicto"] == "UNVERIFIABLE"
    assert res["score_global"] == 0.0
    assert any(p["severidad"] == "CRITICO" for p in res["problemas"])


def test_campos_requeridos_presentes_y_ausentes():
    pdf = _pdf_con_texto("Contrato JUEZ-TEST-0001 tipo INICIAL")
    # presente
    ok = evaluate_pdf(pdf, campos_requeridos=["JUEZ-TEST-0001"])
    assert ok["veredicto"] == "OK"
    # ausente -> problema ALTO -> baja el veredicto
    falla = evaluate_pdf(pdf, campos_requeridos=["NO-EXISTE-9999"])
    assert falla["veredicto"] in ("WARN", "FAIL")
    assert any("NO-EXISTE-9999" in p["descripcion"] for p in falla["problemas"])


def test_ambientes_presentes():
    pdf = _pdf_con_texto("Cocina y Baño principal")
    res = evaluate_pdf(pdf, ambientes=["Cocina", "Baño principal"])
    assert res["veredicto"] == "OK"
    res2 = evaluate_pdf(pdf, ambientes=["Cocina", "Garaje"])
    assert any("Garaje" in p["descripcion"] for p in res2["problemas"])


def test_conteo_fotos_detecta_faltantes():
    pdf = _pdf_con_texto("Inventario", con_imagen=True)  # 1 imagen embebida
    # esperamos 1 -> ok
    ok = evaluate_pdf(pdf, fotos_esperadas=1)
    assert ok["metricas"]["fotos_embebidas"] == 1
    assert ok["veredicto"] == "OK"
    # esperamos 3 -> faltan 2 -> CRITICO -> FAIL
    falla = evaluate_pdf(pdf, fotos_esperadas=3)
    assert falla["veredicto"] == "FAIL"
    assert falla["metricas"]["fotos_esperadas"] == 3
    assert any("Faltan" in p["descripcion"] for p in falla["problemas"])
