"""Inspector de PDFs usando PyMuPDF (`fitz`).

NOTA DE PROCEDENCIA:
    Las funciones puras de bajo nivel están LIFTED de
    `evaluation/artifact/pdf_checks.py` (Juez). Se duplican aquí para
    mantener aislamiento total — el verificador no importa nada del Juez.
    Última sincronización: 2026-05-29.

    Si se corrige un bug en una función duplicada, considerar replicarlo
    en el módulo origen del Juez (y viceversa). El test de paridad opcional
    en `tests/test_parity_with_judge.py` ayuda a detectar drift.

Trabajo NUEVO (no existe en el Juez):
    - `_check_fotos_por_ambiente`: cuenta imágenes embebidas distribuidas
      por header de ambiente. Es el principal valor diagnóstico que el
      verificador agrega sobre el QA sintético del Juez.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from ..schemas import (
    CheckResult,
    ExpectedSnapshot,
    InspectorReport,
    Issue,
    Severidad,
    Verdict,
)
from . import register_inspector
from .base import BaseInspector, InspectorError

log = logging.getLogger("verificador.inspectors.pdf")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de PyMuPDF (lifted de evaluation/artifact/pdf_checks.py)
# ─────────────────────────────────────────────────────────────────────────────

def _abrir(blob: bytes):
    """Abre el PDF en memoria. Lanza fitz.FileDataError o similar si está corrupto."""
    import fitz  # import lazy: solo cuando hay PDF
    return fitz.open(stream=blob, filetype="pdf")


def _contar_imagenes_unicas(doc) -> int:
    """Cuenta imágenes embebidas únicas (por xref). Una imagen reusada en
    varias páginas cuenta como 1."""
    xrefs = set()
    for page in doc:
        for img in page.get_images(full=True):
            xrefs.add(img[0])
    return len(xrefs)


def _imagenes_por_pagina(doc) -> List[int]:
    """Lista con el conteo de imágenes referenciadas por cada página
    (incluye duplicadas por página). Usado para distribución por ambiente."""
    return [len(page.get_images(full=True)) for page in doc]


def _texto_por_pagina(doc) -> List[str]:
    """Texto extraído por página, en minúsculas (para matching case-insensitive)."""
    return [page.get_text().lower() for page in doc]


# ─────────────────────────────────────────────────────────────────────────────
# Checks individuales — retornan (score, issues, metrics) crudo
# ─────────────────────────────────────────────────────────────────────────────

def _check_integridad(blob: bytes) -> Tuple[float, List[Issue], Dict[str, Any]]:
    try:
        doc = _abrir(blob)
    except Exception as exc:
        return 0.0, [Issue(
            severidad=Severidad.CRITICO,
            mensaje=f"PDF ilegible o corrupto: {type(exc).__name__}: {exc}",
            check="integridad",
        )], {}
    try:
        if doc.needs_pass:
            return 0.0, [Issue(
                severidad=Severidad.CRITICO,
                mensaje="PDF encriptado — el verificador no descifra contenido",
                check="integridad",
                detalles={"razon": "pdf_encriptado"},
            )], {}
        n = doc.page_count
    finally:
        doc.close()
    if n <= 0:
        return 0.0, [Issue(
            severidad=Severidad.CRITICO,
            mensaje="PDF sin páginas",
            check="integridad",
        )], {}
    return 1.0, [], {"paginas": n}


def _check_conteo_fotos(blob: bytes, esperadas: int) -> Tuple[float, List[Issue], Dict[str, Any]]:
    doc = _abrir(blob)
    try:
        embebidas = _contar_imagenes_unicas(doc)
    finally:
        doc.close()
    met = {"fotos_esperadas": esperadas, "fotos_embebidas": embebidas}
    if esperadas <= 0:
        return 1.0, [], met
    if embebidas < esperadas:
        faltan = esperadas - embebidas
        sev = Severidad.CRITICO if faltan > 1 else Severidad.ALTO
        return round(embebidas / esperadas, 3), [Issue(
            severidad=sev,
            mensaje=(
                f"Faltan {faltan} foto(s) en el PDF: se esperaban {esperadas} "
                f"y se embebieron {embebidas} (posibles fotos caídas en el pool)"
            ),
            check="conteo_fotos_total",
            detalles=met,
        )], met
    if embebidas > esperadas:
        return 1.0, [Issue(
            severidad=Severidad.MEDIO,
            mensaje=(
                f"El PDF tiene {embebidas} imágenes pero la BD esperaba {esperadas} "
                "(posibles imágenes duplicadas o decorativas)"
            ),
            check="conteo_fotos_total",
            detalles=met,
        )], met
    return 1.0, [], met


def _check_estructura_ambientes(blob: bytes, ambientes: List[str]) -> Tuple[float, List[Issue], Dict[str, Any]]:
    if not ambientes:
        return 1.0, [], {}
    doc = _abrir(blob)
    try:
        texto = "\n".join(_texto_por_pagina(doc))
    finally:
        doc.close()
    faltantes = [a for a in ambientes if a.lower() not in texto]
    presentes = len(ambientes) - len(faltantes)
    issues = [
        Issue(
            severidad=Severidad.ALTO,
            mensaje=f"Ambiente '{a}' no aparece en el PDF",
            check="ambientes_presentes",
        )
        for a in faltantes
    ]
    return (
        round(presentes / len(ambientes), 3),
        issues,
        {"ambientes_presentes": presentes, "ambientes_faltantes": faltantes,
         "ambientes_esperados": ambientes},
    )


def _check_campos_requeridos(blob: bytes, campos: List[str]) -> Tuple[float, List[Issue], Dict[str, Any]]:
    if not campos:
        return 1.0, [], {}
    doc = _abrir(blob)
    try:
        texto = "\n".join(_texto_por_pagina(doc))
    finally:
        doc.close()
    faltantes = [c for c in campos if str(c).lower() not in texto]
    presentes = len(campos) - len(faltantes)
    issues = [
        Issue(
            severidad=Severidad.ALTO,
            mensaje=f"Campo requerido '{c}' ausente en el PDF",
            check="campos_requeridos",
        )
        for c in faltantes
    ]
    return (
        round(presentes / len(campos), 3),
        issues,
        {"campos_faltantes": faltantes},
    )


def _check_fotos_por_ambiente(
    blob: bytes,
    fotos_por_ambiente: Dict[str, int],
) -> Tuple[float, List[Issue], Dict[str, Any]]:
    """Distribución de imágenes por ambiente. NUEVO — no existe en pdf_checks.py.

    Estrategia: por cada página, detectar qué ambiente (de los esperados)
    aparece en el texto y atribuir las imágenes de esa página a ese ambiente.
    Si una página no matchea ninguno, las imágenes van a 'indeterminado'.

    Limitación conocida (MVP): si una página contiene texto de dos ambientes,
    se atribuye al primero matcheado (en orden de aparición en el dict).
    """
    if not fotos_por_ambiente:
        return 1.0, [], {}

    doc = _abrir(blob)
    try:
        textos_pagina = _texto_por_pagina(doc)
        imgs_pagina = _imagenes_por_pagina(doc)
    finally:
        doc.close()

    ambientes_keys = list(fotos_por_ambiente.keys())
    conteo: Dict[str, int] = {a: 0 for a in ambientes_keys}
    indeterminado = 0
    for texto, n_imgs in zip(textos_pagina, imgs_pagina):
        if n_imgs == 0:
            continue
        ambiente_match = next((a for a in ambientes_keys if a.lower() in texto), None)
        if ambiente_match is None:
            indeterminado += n_imgs
        else:
            conteo[ambiente_match] += n_imgs

    issues: List[Issue] = []
    diferencias: Dict[str, Dict[str, int]] = {}
    score_partial = 0.0
    total_esperado = 0

    for amb, esperado in fotos_por_ambiente.items():
        observado = conteo.get(amb, 0)
        diferencias[amb] = {"esperado": esperado, "observado": observado}
        total_esperado += esperado
        if esperado <= 0:
            score_partial += 1.0
            continue
        ratio = min(observado / esperado, 1.0)
        score_partial += ratio
        diff = esperado - observado
        if diff > 0:
            sev = Severidad.ALTO if diff >= 2 else Severidad.MEDIO
            issues.append(Issue(
                severidad=sev,
                mensaje=(
                    f"Ambiente '{amb}': faltan {diff} foto(s) — esperadas {esperado}, "
                    f"observadas {observado} en el PDF"
                ),
                check="fotos_por_ambiente",
                detalles={"ambiente": amb, "esperado": esperado, "observado": observado},
            ))

    score = score_partial / len(fotos_por_ambiente) if fotos_por_ambiente else 1.0
    metrics = {
        "fotos_por_ambiente": diferencias,
        "indeterminado": indeterminado,
        "total_esperado": total_esperado,
    }
    return round(score, 3), issues, metrics


# ─────────────────────────────────────────────────────────────────────────────
# Consolidación de verdicts
# ─────────────────────────────────────────────────────────────────────────────

def _verdict_por_check(score: float, issues: List[Issue]) -> Verdict:
    if any(i.severidad == Severidad.CRITICO for i in issues):
        return Verdict.FAIL
    if score >= 0.95 and not any(i.severidad in (Severidad.ALTO,) for i in issues):
        return Verdict.OK
    if score >= 0.70:
        return Verdict.WARN
    return Verdict.FAIL


def _verdict_global(checks: List[CheckResult]) -> Tuple[Verdict, float]:
    if not checks:
        return Verdict.UNVERIFIABLE, 0.0
    scores = [c.score for c in checks]
    overall_score = round(sum(scores) / len(scores), 3)
    has_critico = any(
        i.severidad == Severidad.CRITICO
        for c in checks for i in c.issues
    )
    has_alto = any(
        i.severidad == Severidad.ALTO
        for c in checks for i in c.issues
    )
    has_medio = any(
        i.severidad == Severidad.MEDIO
        for c in checks for i in c.issues
    )
    if has_critico or overall_score < 0.70:
        verdict = Verdict.FAIL
    elif overall_score >= 0.95 and not has_alto and not has_medio:
        verdict = Verdict.OK
    elif overall_score >= 0.70:
        verdict = Verdict.WARN
    else:
        verdict = Verdict.FAIL
    return verdict, overall_score


# ─────────────────────────────────────────────────────────────────────────────
# Inspector público
# ─────────────────────────────────────────────────────────────────────────────

class PdfInspector:
    """Audita un PDF contra un ExpectedSnapshot. Implementa `BaseInspector`."""

    artifact_type = "pdf"

    def inspect(self, blob: bytes, expected: ExpectedSnapshot) -> InspectorReport:
        if not blob:
            raise InspectorError("PDF vacío (0 bytes)")

        checks: List[CheckResult] = []

        # 1. Integridad. Si el PDF no se puede abrir, no tiene sentido seguir.
        score, issues, metrics = _check_integridad(blob)
        integridad = CheckResult(
            name="integridad",
            verdict=_verdict_por_check(score, issues),
            score=score,
            metrics=metrics,
            issues=issues,
        )
        checks.append(integridad)
        if integridad.verdict == Verdict.FAIL and any(
            i.severidad == Severidad.CRITICO for i in integridad.issues
        ):
            # PDF roto: emitir reporte temprano con UNVERIFIABLE
            return InspectorReport(
                checks=checks,
                overall_verdict=Verdict.UNVERIFIABLE,
                overall_score=0.0,
            )

        # 2. Conteo de fotos total
        n_fotos = expected.counts.get("fotos", 0)
        if n_fotos > 0:
            score, issues, metrics = _check_conteo_fotos(blob, n_fotos)
            checks.append(CheckResult(
                name="conteo_fotos_total",
                verdict=_verdict_por_check(score, issues),
                score=score, metrics=metrics, issues=issues,
            ))

        # 3. Ambientes presentes (lista de nombres esperados en structure)
        ambientes = expected.structure.get("ambientes") or []
        if ambientes:
            score, issues, metrics = _check_estructura_ambientes(blob, ambientes)
            checks.append(CheckResult(
                name="ambientes_presentes",
                verdict=_verdict_por_check(score, issues),
                score=score, metrics=metrics, issues=issues,
            ))

        # 4. Campos requeridos (contrato_id, propietario, etc.)
        if expected.required_strings:
            score, issues, metrics = _check_campos_requeridos(blob, expected.required_strings)
            checks.append(CheckResult(
                name="campos_requeridos",
                verdict=_verdict_por_check(score, issues),
                score=score, metrics=metrics, issues=issues,
            ))

        # 5. Distribución de fotos por ambiente (NUEVO)
        fotos_por_amb = expected.structure.get("fotos_por_ambiente") or {}
        if isinstance(fotos_por_amb, dict) and fotos_por_amb:
            score, issues, metrics = _check_fotos_por_ambiente(blob, fotos_por_amb)
            checks.append(CheckResult(
                name="fotos_por_ambiente",
                verdict=_verdict_por_check(score, issues),
                score=score, metrics=metrics, issues=issues,
            ))

        verdict, score_global = _verdict_global(checks)
        return InspectorReport(
            checks=checks,
            overall_verdict=verdict,
            overall_score=score_global,
        )


# Auto-registro al importar el módulo
register_inspector("pdf", PdfInspector)
