"""Tests del PdfInspector con fixtures sintéticos.

Cero PDFs reales de clientes — todo se genera en `conftest.py` con reportlab.
"""
from __future__ import annotations

from verificador.inspectors.pdf import PdfInspector
from verificador.schemas import ExpectedSnapshot, Severidad, Verdict


def _expected(
    artifact_id: str = "INV-1",
    fotos: int = 9,
    ambientes=("Cocina", "Sala", "Baño"),
    required_strings=("CONTRATO-OK", "Juan Tester"),
    fotos_por_ambiente=None,
) -> ExpectedSnapshot:
    return ExpectedSnapshot(
        artifact_id=artifact_id,
        counts={"fotos": fotos},
        structure={
            "ambientes": list(ambientes),
            **({"fotos_por_ambiente": fotos_por_ambiente} if fotos_por_ambiente else {}),
        },
        required_strings=list(required_strings),
    )


def test_pdf_ok_verdict_ok(pdf_ok):
    expected = _expected(artifact_id="INV-OK", fotos=9)
    report = PdfInspector().inspect(pdf_ok, expected)

    assert report.overall_verdict == Verdict.OK, [
        (c.name, c.verdict, [(i.severidad, i.mensaje) for i in c.issues]) for c in report.checks
    ]
    assert report.overall_score >= 0.95
    nombres = {c.name for c in report.checks}
    assert "integridad" in nombres
    assert "conteo_fotos_total" in nombres
    assert "ambientes_presentes" in nombres
    assert "campos_requeridos" in nombres


def test_pdf_falta_una_foto_es_warn_o_fail(pdf_falta_foto):
    """Falta 1 foto sobre 9 esperadas → conteo da score 8/9 ≈ 0.889 con issue ALTO,
    eso lleva a WARN (>= 0.7) o FAIL si suma con otros — verificamos que NO sea OK."""
    expected = _expected(artifact_id="INV-FALTA", fotos=9,
                         required_strings=("CONTRATO-FALTA", "Juan Tester"))
    report = PdfInspector().inspect(pdf_falta_foto, expected)

    assert report.overall_verdict in (Verdict.WARN, Verdict.FAIL)
    # Verificar que el check específico detectó el faltante
    conteo = next(c for c in report.checks if c.name == "conteo_fotos_total")
    assert conteo.metrics["fotos_esperadas"] == 9
    assert conteo.metrics["fotos_embebidas"] == 8
    assert any(i.severidad in (Severidad.ALTO, Severidad.CRITICO) for i in conteo.issues)


def test_pdf_sin_firmas_falta_campo_requerido(pdf_sin_firmas):
    """El PDF no tiene 'Firma' — required_strings con esa cadena debe fallar."""
    expected = ExpectedSnapshot(
        artifact_id="INV-SINFIRMA",
        counts={"fotos": 9},
        structure={"ambientes": ["Cocina", "Sala", "Baño"]},
        required_strings=["CONTRATO-SINFIRMA", "Firma del propietario"],
    )
    report = PdfInspector().inspect(pdf_sin_firmas, expected)

    campos = next(c for c in report.checks if c.name == "campos_requeridos")
    assert "firma del propietario" in [s.lower() for s in campos.metrics["campos_faltantes"]]
    assert any(i.severidad == Severidad.ALTO for i in campos.issues)


def test_pdf_corrupto_devuelve_unverifiable(pdf_corrupto):
    expected = _expected(artifact_id="INV-CORRUPTO")
    report = PdfInspector().inspect(pdf_corrupto, expected)

    assert report.overall_verdict == Verdict.UNVERIFIABLE
    integridad = next(c for c in report.checks if c.name == "integridad")
    assert integridad.score == 0.0
    assert any(i.severidad == Severidad.CRITICO for i in integridad.issues)


def test_fotos_por_ambiente_detecta_falta_localizada(pdf_falta_foto):
    """A la cocina le falta una foto. fotos_por_ambiente debe atribuirlo a 'Cocina'."""
    expected = _expected(
        artifact_id="INV-FALTA",
        fotos=9,
        required_strings=("CONTRATO-FALTA",),
        fotos_por_ambiente={"Cocina": 3, "Sala": 3, "Baño": 3},
    )
    report = PdfInspector().inspect(pdf_falta_foto, expected)

    distrib = next(c for c in report.checks if c.name == "fotos_por_ambiente")
    detalles = distrib.metrics["fotos_por_ambiente"]
    assert detalles["Cocina"]["esperado"] == 3
    assert detalles["Cocina"]["observado"] == 2
    assert detalles["Sala"]["observado"] == 3
    assert detalles["Baño"]["observado"] == 3
    # Debe haber un issue específico para Cocina
    assert any("Cocina" in i.mensaje for i in distrib.issues)


def test_blob_vacio_levanta_inspector_error():
    import pytest
    from verificador.inspectors.base import InspectorError

    with pytest.raises(InspectorError):
        PdfInspector().inspect(b"", _expected())


def test_inspector_registrado():
    from verificador.inspectors import get_inspector

    insp = get_inspector("pdf")
    assert insp.artifact_type == "pdf"
