"""Construye un PDF sintético "como si lo hubiera generado el agente".

Reproduce los conteos esperados (ambientes, fotos por ambiente, campos
requeridos) para que el Verificador audite contra el ExpectedSnapshot.

Deliberadamente simple: no busca verse "bonito", busca ser **audit-able**.
Si el agente bajo test "decidió" registrar Cocina con 5 fotos y Sala con 8,
el PDF resultante tendrá esa estructura. Cualquier discrepancia entre
canonical y lo que el agente realmente hizo (vía MockToolRunner.calls) se
puede usar para detectar bugs del agente sin ejecutar producción.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from . import pdf_cache

log = logging.getLogger("juez.synthetic.pdf_builder")

_COLORES = [
    "red", "green", "blue", "yellow", "purple", "cyan",
    "orange", "pink", "brown", "gray", "magenta", "olive",
    "navy", "teal", "lime", "maroon", "coral", "darkgreen",
]


def _imagen_unica(idx: int) -> ImageReader:
    """Cada imagen embebida debe tener bytes únicos para que PyMuPDF la
    cuente como xref distinto (igual que en fixtures del Verificador)."""
    color = _COLORES[idx % len(_COLORES)]
    size = (100 + idx, 100 + idx)  # tamaño levemente distinto por imagen
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def build_synthetic_pdf(
    canonical: Dict[str, Any],
    tool_calls: List[Dict[str, Any]] | None = None,
    cache_key: Optional[str] = None,
) -> bytes:
    """Genera un PDF en memoria con las secciones esperadas.

    Args:
        canonical: dict de `snapshot_factory.make_synthetic_data` con
                   contrato_id, propietario, ambientes, fotos_por_ambiente, etc.
        tool_calls: log opcional de tool calls del agente (no se renderiza,
                    se loguea para debug).
        cache_key: si se pasa, intenta leer del cache filesystem antes de
                   regenerar; si miss, escribe el resultado. None = sin cache
                   (comportamiento legacy).

    Returns:
        Bytes del PDF. Listo para base64 encode + payload del Verificador.
    """
    if cache_key is not None:
        cached = pdf_cache.get(cache_key)
        if cached is not None:
            log.info(
                "pdf_builder cache hit contrato_id=%s key=%s bytes=%d",
                canonical.get("contrato_id"), cache_key, len(cached),
            )
            return cached

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"Inventario {canonical['contrato_id']}")

    # ── Portada ──────────────────────────────────────────────────────────
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 720, f"Inventario {canonical['contrato_id']}")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, f"Propietario: {canonical['propietario']}")
    c.drawString(72, 670, f"Arrendatario: {canonical['arrendatario']}")
    c.drawString(72, 650, f"Tipo: {canonical['tipo_inventario']}")
    c.drawString(72, 630, f"Inventario ID: {canonical['inventario_id']}")
    c.drawString(72, 600, f"Ambientes registrados: {len(canonical['ambientes'])}")
    c.drawString(72, 580, f"Total de fotos: {canonical['total_fotos']}")
    c.showPage()

    # ── Una página por ambiente con sus fotos embebidas ──────────────────
    img_idx = 0
    for ambiente in canonical["ambientes"]:
        n_fotos = canonical["fotos_por_ambiente"].get(ambiente, 0)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 720, f"Ambiente: {ambiente}")
        c.setFont("Helvetica", 10)
        c.drawString(72, 700, f"Cantidad de fotos: {n_fotos}")

        x0, y0 = 72, 400
        for i in range(n_fotos):
            img = _imagen_unica(img_idx)
            x = x0 + (i % 4) * 110
            y = y0 - (i // 4) * 110
            c.drawImage(img, x, y, width=100, height=100)
            img_idx += 1
        c.showPage()

    # ── Página final con firma ──────────────────────────────────────────
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 720, "Aprobación")
    c.setFont("Helvetica", 10)
    c.drawString(72, 700, f"Inventario aprobado por: {canonical['propietario']}")
    c.drawString(72, 680, "Firma del propietario: _______________________")
    c.drawString(72, 660, "Fecha: ______________")
    c.showPage()

    c.save()
    blob = buf.getvalue()
    log.info(
        "pdf_builder ok contrato_id=%s bytes=%d ambientes=%d fotos=%d tool_calls=%d",
        canonical["contrato_id"], len(blob),
        len(canonical["ambientes"]), canonical["total_fotos"],
        len(tool_calls or []),
    )

    if cache_key is not None:
        try:
            pdf_cache.put(cache_key, blob)
        except OSError as e:
            # Cache failures no deben romper el flujo principal.
            log.warning("pdf_builder cache put failed key=%s err=%s", cache_key, e)

    return blob
