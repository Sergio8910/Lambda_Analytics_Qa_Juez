"""Tests unitarios para pdf_builder.

Cubren:
  - PDF generado abre con PyMuPDF (fitz).
  - Imágenes únicas por xref == canonical['total_fotos'].
  - N de páginas == 2 + len(ambientes) (portada + ambientes + firma).
  - Texto extraído contiene contrato_id, propietario, "INICIAL" y cada room_name.
  - Edge cases: ambientes vacíos y total_fotos=0 no deben crashear.
"""
from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from juez.evaluation.contra_agente.synthetic.pdf_builder import build_synthetic_pdf
from juez.evaluation.contra_agente.synthetic.snapshot_factory import make_synthetic_data


def _abrir(pdf_bytes: bytes) -> fitz.Document:
    return fitz.open(stream=pdf_bytes, filetype="pdf")


# ── Caso "normal" usando snapshot_factory ────────────────────────────────────

def test_pdf_abre_con_pymupdf():
    _, canonical = make_synthetic_data("batch-pdf", 1)
    pdf_bytes = build_synthetic_pdf(canonical)
    assert isinstance(pdf_bytes, bytes) and len(pdf_bytes) > 0
    doc = _abrir(pdf_bytes)
    try:
        # Si fitz pudo abrirlo, el PDF es estructuralmente válido.
        assert doc.page_count > 0
    finally:
        doc.close()


def test_pdf_numero_paginas():
    _, canonical = make_synthetic_data("batch-pdf", 1)
    pdf_bytes = build_synthetic_pdf(canonical)
    doc = _abrir(pdf_bytes)
    try:
        # Portada + 1 página por ambiente + página de firma.
        esperado = 2 + len(canonical["ambientes"])
        assert doc.page_count == esperado
    finally:
        doc.close()


def test_pdf_imagenes_unicas_por_xref_match_total_fotos():
    _, canonical = make_synthetic_data("batch-pdf-img", 1)
    pdf_bytes = build_synthetic_pdf(canonical)
    doc = _abrir(pdf_bytes)
    try:
        xrefs = set()
        for page_idx in range(doc.page_count):
            for img in doc.get_page_images(page_idx, full=True):
                # img[0] es el xref del objeto imagen.
                xrefs.add(img[0])
        assert len(xrefs) == canonical["total_fotos"], (
            f"xrefs únicas={len(xrefs)} vs total_fotos canonical={canonical['total_fotos']}"
        )
    finally:
        doc.close()


def test_pdf_texto_contiene_campos_obligatorios():
    _, canonical = make_synthetic_data("batch-pdf-txt", 1)
    pdf_bytes = build_synthetic_pdf(canonical)
    doc = _abrir(pdf_bytes)
    try:
        full_text = "\n".join(doc.load_page(i).get_text("text") for i in range(doc.page_count))
    finally:
        doc.close()

    assert canonical["contrato_id"] in full_text
    assert canonical["propietario"] in full_text
    assert "INICIAL" in full_text  # tipo_inventario
    for amb in canonical["ambientes"]:
        assert amb in full_text, f"ambiente {amb!r} no aparece en el PDF"


def test_pdf_tool_calls_argumento_opcional_no_crashea():
    _, canonical = make_synthetic_data("batch-pdf-tc", 1)
    tool_calls = [
        {"name": "registrar_ambiente", "args": {"room_name": "Cocina"}},
        {"name": "subir_foto", "args": {"ambiente_id": 1, "url": "fake://x"}},
    ]
    pdf_bytes = build_synthetic_pdf(canonical, tool_calls=tool_calls)
    assert len(pdf_bytes) > 0
    doc = _abrir(pdf_bytes)
    try:
        assert doc.page_count == 2 + len(canonical["ambientes"])
    finally:
        doc.close()


# ── Edge cases ───────────────────────────────────────────────────────────────

def _canonical_base() -> dict:
    return {
        "source": "synthetic",
        "contrato_id": "JUEZ-E2E-EDGE-01",
        "inventario_id": 99001,
        "propietario": "Propietario Sintético",
        "arrendatario": "Arrendatario Sintético",
        "tipo_inventario": "INICIAL",
        "ambientes": ["Cocina"],
        "fotos_por_ambiente": {"Cocina": 2},
        "total_fotos": 2,
    }


def test_pdf_sin_ambientes_no_crashea():
    canonical = _canonical_base()
    canonical["ambientes"] = []
    canonical["fotos_por_ambiente"] = {}
    canonical["total_fotos"] = 0

    pdf_bytes = build_synthetic_pdf(canonical)
    assert len(pdf_bytes) > 0
    doc = _abrir(pdf_bytes)
    try:
        # Portada + (cero páginas de ambientes) + firma == 2
        assert doc.page_count == 2
    finally:
        doc.close()


def test_pdf_total_fotos_cero_no_crashea():
    canonical = _canonical_base()
    canonical["fotos_por_ambiente"] = {"Cocina": 0}
    canonical["total_fotos"] = 0

    pdf_bytes = build_synthetic_pdf(canonical)
    assert len(pdf_bytes) > 0
    doc = _abrir(pdf_bytes)
    try:
        # Portada + 1 ambiente + firma
        assert doc.page_count == 3
        # Cero imágenes embebidas en todo el doc.
        xrefs = set()
        for page_idx in range(doc.page_count):
            for img in doc.get_page_images(page_idx, full=True):
                xrefs.add(img[0])
        assert len(xrefs) == 0
    finally:
        doc.close()


@pytest.mark.parametrize("idx", [1, 2, 7])
def test_pdf_param_distintos_idx(idx):
    """Cada idx genera un PDF válido y con conteos coherentes."""
    _, canonical = make_synthetic_data("batch-param", idx)
    pdf_bytes = build_synthetic_pdf(canonical)
    doc = _abrir(pdf_bytes)
    try:
        assert doc.page_count == 2 + len(canonical["ambientes"])
        xrefs = set()
        for page_idx in range(doc.page_count):
            for img in doc.get_page_images(page_idx, full=True):
                xrefs.add(img[0])
        assert len(xrefs) == canonical["total_fotos"]
    finally:
        doc.close()
