"""Fixtures sintéticos para tests del verificador.

CRÍTICO: estos PDFs son generados localmente con reportlab + Pillow. NO
contienen datos reales de clientes. Cualquier test que necesite un PDF real
debe pedirlo aparte (no incluir en el repo).
"""
from __future__ import annotations

import io
from typing import Dict, List, Optional

import pytest
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def _imagen_dummy(color: str = "red", size: tuple = (200, 200)) -> ImageReader:
    """Genera una imagen PIL en memoria para embeber en PDFs de prueba."""
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


_COLORES = [
    "red", "green", "blue", "yellow", "purple", "cyan",
    "orange", "pink", "brown", "gray", "magenta", "olive",
    "navy", "teal", "lime", "maroon",
]


def _generar_pdf(
    paginas: List[Dict],
    titulo: str = "Inventario de prueba",
    metadata_extra: Optional[Dict[str, str]] = None,
) -> bytes:
    """Genera un PDF sintético en memoria.

    `paginas` es una lista de dicts con:
      - `header`: string que va arriba (ej. "Ambiente: Cocina")
      - `imagenes`: int, cuántas imágenes dummy embeber
      - `body`: string adicional opcional

    Cada imagen embebida es ÚNICA (color + tamaño distintos) para que
    PyMuPDF las cuente por separado vía xref. En PDFs reales de Abad
    esto ocurre naturalmente porque cada foto del inventarista tiene
    bytes únicos.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(titulo)
    if metadata_extra:
        for k, v in metadata_extra.items():
            c.setKeywords(f"{k}={v}")
    img_idx_global = 0
    for idx, pagina in enumerate(paginas):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 720, pagina.get("header", f"Página {idx + 1}"))
        c.setFont("Helvetica", 10)
        body = pagina.get("body", "")
        if body:
            y = 700
            for line in body.split("\n"):
                c.drawString(72, y, line)
                y -= 14
        x, y = 72, 400
        for i in range(pagina.get("imagenes", 0)):
            color = _COLORES[img_idx_global % len(_COLORES)]
            # Tamaño levemente distinto por imagen para garantizar xref único
            size = (100 + img_idx_global, 100 + img_idx_global)
            img = _imagen_dummy(color=color, size=size)
            c.drawImage(img, x + (i % 4) * 110, y - (i // 4) * 110, width=100, height=100)
            img_idx_global += 1
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


@pytest.fixture
def pdf_ok() -> bytes:
    """PDF con 3 ambientes, 3 fotos cada uno. Esperado: 9 fotos totales."""
    return _generar_pdf(
        titulo="Inventario CONTRATO-OK",
        paginas=[
            {"header": "Portada", "body": "Contrato: CONTRATO-OK\nPropietario: Juan Tester\nFecha: 2026-05-29", "imagenes": 0},
            {"header": "Ambiente: Cocina", "body": "Items: nevera, estufa, mesa", "imagenes": 3},
            {"header": "Ambiente: Sala", "body": "Items: sofá, mesa, lámpara", "imagenes": 3},
            {"header": "Ambiente: Baño", "body": "Items: lavamanos, espejo, ducha", "imagenes": 3},
            {"header": "Firma", "body": "Firma del propietario: _______________", "imagenes": 0},
        ],
    )


@pytest.fixture
def pdf_falta_foto() -> bytes:
    """Mismo que pdf_ok pero a la cocina le falta 1 foto. 8 fotos totales."""
    return _generar_pdf(
        titulo="Inventario CONTRATO-FALTA",
        paginas=[
            {"header": "Portada", "body": "Contrato: CONTRATO-FALTA\nPropietario: Juan Tester\nFecha: 2026-05-29", "imagenes": 0},
            {"header": "Ambiente: Cocina", "body": "Items: nevera, estufa, mesa", "imagenes": 2},  # <-- falta 1
            {"header": "Ambiente: Sala", "body": "Items: sofá, mesa, lámpara", "imagenes": 3},
            {"header": "Ambiente: Baño", "body": "Items: lavamanos, espejo, ducha", "imagenes": 3},
            {"header": "Firma", "body": "Firma del propietario: _______________", "imagenes": 0},
        ],
    )


@pytest.fixture
def pdf_sin_firmas() -> bytes:
    """PDF sin la sección de firmas — falta un campo requerido."""
    return _generar_pdf(
        titulo="Inventario CONTRATO-SINFIRMA",
        paginas=[
            {"header": "Portada", "body": "Contrato: CONTRATO-SINFIRMA\nPropietario: Ana Pruebas\nFecha: 2026-05-29", "imagenes": 0},
            {"header": "Ambiente: Cocina", "imagenes": 3},
            {"header": "Ambiente: Sala", "imagenes": 3},
            {"header": "Ambiente: Baño", "imagenes": 3},
            # Sin página de Firma
        ],
    )


@pytest.fixture
def pdf_corrupto() -> bytes:
    """Bytes que NO son un PDF válido."""
    return b"este no es un pdf, solo texto plano que pretende serlo"
