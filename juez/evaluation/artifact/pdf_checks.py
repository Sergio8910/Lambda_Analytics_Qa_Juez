"""Inspeccion de PDFs para QA de artefactos (usa PyMuPDF / fitz).

Funciones puras y testables: reciben bytes o ruta de un PDF y retornan
CheckResult. No conocen n8n ni el Juez — reutilizables por cualquier evaluador.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Union

from .protocol import Issue, mk_issue

PdfInput = Union[str, bytes]


@dataclass
class CheckResult:
    score: float                       # 0..1
    issues: List[Issue] = field(default_factory=list)
    metricas: Dict[str, Any] = field(default_factory=dict)


def _abrir(pdf: PdfInput):
    import fitz  # lazy: solo si hay PDF que inspeccionar
    if isinstance(pdf, bytes):
        return fitz.open(stream=pdf, filetype="pdf")
    return fitz.open(pdf)


def contar_imagenes_embebidas(pdf: PdfInput) -> int:
    doc = _abrir(pdf)
    try:
        xrefs = set()
        for page in doc:
            for img in page.get_images(full=True):
                xrefs.add(img[0])  # xref unico por imagen embebida
        return len(xrefs)
    finally:
        doc.close()


def extraer_texto(pdf: PdfInput) -> str:
    doc = _abrir(pdf)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def verificar_integridad(pdf: PdfInput) -> CheckResult:
    try:
        doc = _abrir(pdf)
    except Exception as exc:
        return CheckResult(0.0, [mk_issue("CRITICO", f"PDF ilegible o corrupto: {exc}",
                                          tipo="Artefacto / PDF")])
    try:
        n = doc.page_count
    finally:
        doc.close()
    if n <= 0:
        return CheckResult(0.0, [mk_issue("CRITICO", "PDF sin paginas",
                                          tipo="Artefacto / PDF")])
    return CheckResult(1.0, [], {"paginas": n})


def verificar_conteo_fotos(pdf: PdfInput, esperadas: int) -> CheckResult:
    """Chequeo estrella: N fotos esperadas -> N embebidas. Detecta fotos caidas."""
    embebidas = contar_imagenes_embebidas(pdf)
    met = {"fotos_esperadas": esperadas, "fotos_embebidas": embebidas}
    if esperadas <= 0:
        return CheckResult(1.0, [], met)
    if embebidas < esperadas:
        faltan = esperadas - embebidas
        sev = "CRITICO" if faltan > 1 else "ALTO"
        return CheckResult(
            round(embebidas / esperadas, 3),
            [mk_issue(sev, f"Faltan {faltan} foto(s) en el PDF: se esperaban "
                           f"{esperadas} y se embebieron {embebidas} (fotos caidas)",
                      tipo="Artefacto / PDF")], met)
    if embebidas > esperadas:
        return CheckResult(1.0, [mk_issue(
            "MEDIO", f"El PDF tiene {embebidas} imagenes pero se esperaban {esperadas} "
                     "(posibles imagenes duplicadas o decorativas)",
            tipo="Artefacto / PDF")], met)
    return CheckResult(1.0, [], met)


def verificar_estructura_por_ambiente(pdf: PdfInput, ambientes: List[str]) -> CheckResult:
    """Verifica que cada ambiente esperado aparezca en el texto del PDF."""
    if not ambientes:
        return CheckResult(1.0, [], {})
    texto = extraer_texto(pdf).lower()
    faltantes = [a for a in ambientes if a.lower() not in texto]
    presentes = len(ambientes) - len(faltantes)
    issues: List[Issue] = []
    for a in faltantes:
        issues.append(mk_issue("ALTO", f"Ambiente '{a}' no aparece en el PDF",
                               tipo="Artefacto / PDF"))
    return CheckResult(round(presentes / len(ambientes), 3), issues,
                       {"ambientes_presentes": presentes, "ambientes_faltantes": faltantes})


def verificar_campos_requeridos(pdf: PdfInput, campos: List[str]) -> CheckResult:
    """Verifica que cadenas requeridas (contrato_id, tipo, etc.) esten en el PDF."""
    if not campos:
        return CheckResult(1.0, [], {})
    texto = extraer_texto(pdf).lower()
    faltantes = [c for c in campos if str(c).lower() not in texto]
    presentes = len(campos) - len(faltantes)
    issues = [mk_issue("ALTO", f"Campo requerido '{c}' ausente en el PDF",
                       tipo="Artefacto / PDF") for c in faltantes]
    return CheckResult(round(presentes / len(campos), 3), issues,
                       {"campos_faltantes": faltantes})
