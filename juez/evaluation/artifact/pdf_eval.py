"""Evaluación AUTÓNOMA de un PDF — sin BD, sin Drive, sin spec por-agente.

Le das los bytes de un PDF + qué esperas (nº de fotos, ambientes, campos) y
devuelve score + veredicto + problemas, reusando los chequeos puros de
`pdf_checks`. Pensado para el endpoint "sube el PDF y lo evalúo solo".

Es ADITIVO: no toca el flujo de artefactos de Abad; solo orquesta los checks
que ya existen sobre bytes que llegan por cualquier vía (upload, url, base64).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import pdf_checks as pc
from .protocol import Issue

# Severidades ordenadas para decidir el veredicto.
_SEV_RANK = {"CRITICO": 3, "ALTO": 2, "MEDIO": 1, "BAJO": 0}

_LINEA = "=" * 70


def render_pdf_report_txt(resultado: Dict[str, Any], source_name: str = "") -> str:
    """Renderiza el resultado de `evaluate_pdf` como reporte TXT legible.

    Mismo estilo que el resto de reportes del Juez (cabecera con '='), listo
    para guardar en outputs/ o devolver en la respuesta del API.
    """
    L: List[str] = []
    L.append(_LINEA)
    L.append("  EVALUACIÓN DE PDF (artefacto generado)")
    L.append(_LINEA)
    if source_name:
        L.append(f"  Archivo            : {source_name}")
    L.append(f"  Veredicto          : {resultado.get('veredicto', '?')}")
    L.append(f"  Score              : {resultado.get('score_global', 0.0):.1f}/100")

    met = resultado.get("metricas", {})
    if met:
        L.append("")
        L.append("  Métricas:")
        if "paginas" in met:
            L.append(f"     Páginas           : {met['paginas']}")
        if "fotos_esperadas" in met or "fotos_embebidas" in met:
            L.append(f"     Fotos esperadas   : {met.get('fotos_esperadas', '?')}")
            L.append(f"     Fotos embebidas   : {met.get('fotos_embebidas', '?')}")
        if met.get("ambientes_faltantes"):
            L.append(f"     Ambientes faltantes: {', '.join(map(str, met['ambientes_faltantes']))}")
        if met.get("campos_faltantes"):
            L.append(f"     Campos faltantes  : {', '.join(map(str, met['campos_faltantes']))}")

    L.append("")
    L.append("  Chequeos:")
    for c in resultado.get("checks", []):
        L.append(f"     - {c.get('check'):<20} score {c.get('score', 0.0):.2f}")

    problemas = resultado.get("problemas", [])
    if problemas:
        L.append("")
        L.append("  Problemas detectados:")
        for p in problemas:
            L.append(f"     [{p.get('severidad')}] {p.get('descripcion')}")
    else:
        L.append("")
        L.append("  Sin problemas detectados.")

    nota = resultado.get("nota_metodo")
    if nota:
        L.append("")
        L.append(f"  Nota: {nota}")

    L.append(_LINEA)
    return "\n".join(L)


def evaluate_pdf(
    blob: bytes,
    *,
    fotos_esperadas: int = 0,
    ambientes: Optional[List[str]] = None,
    campos_requeridos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Evalúa un PDF contra expectativas simples. Cero dependencias externas.

    Args:
        blob: bytes del PDF.
        fotos_esperadas: nº de imágenes embebidas que debería tener (0 = no chequear).
        ambientes: nombres que deben aparecer en el texto del PDF.
        campos_requeridos: cadenas (contrato_id, etc.) que deben aparecer.

    Returns:
        dict con: score_global (0..100), veredicto (OK/WARN/FAIL/UNVERIFIABLE),
        checks (lista nombre+score+issues), problemas (planos), metricas.
    """
    ambientes = ambientes or []
    campos_requeridos = campos_requeridos or []

    checks: List[Dict[str, Any]] = []
    problemas: List[Issue] = []
    metricas: Dict[str, Any] = {}

    def _add(nombre: str, res: pc.CheckResult) -> None:
        checks.append({"check": nombre, "score": res.score, "issues": res.issues})
        problemas.extend(res.issues)
        metricas.update(res.metricas)

    # 1) Integridad — si falla, no tiene sentido seguir.
    integ = pc.verificar_integridad(blob)
    _add("integridad", integ)
    if integ.score == 0.0:
        return {
            "score_global": 0.0,
            "veredicto": "UNVERIFIABLE",
            "checks": checks,
            "problemas": problemas,
            "metricas": metricas,
            "nota_metodo": (
                "Evaluación autónoma de PDF (sin BD/Drive/spec). El PDF no se "
                "pudo abrir; no se ejecutaron más chequeos."
            ),
        }

    # 2) Conteo de fotos
    if fotos_esperadas and fotos_esperadas > 0:
        _add("conteo_fotos", pc.verificar_conteo_fotos(blob, fotos_esperadas))

    # 3) Ambientes presentes
    if ambientes:
        _add("ambientes", pc.verificar_estructura_por_ambiente(blob, ambientes))

    # 4) Campos requeridos
    if campos_requeridos:
        _add("campos_requeridos", pc.verificar_campos_requeridos(blob, campos_requeridos))

    # Agregación
    scores = [c["score"] for c in checks]
    score_global = round(sum(scores) / len(scores) * 100, 1) if scores else 0.0
    peor = max((_SEV_RANK.get(p.get("severidad", "BAJO"), 0) for p in problemas), default=-1)

    if peor == _SEV_RANK["CRITICO"] or score_global < 70:
        veredicto = "FAIL"
    elif score_global >= 95 and peor < _SEV_RANK["MEDIO"]:
        veredicto = "OK"
    else:
        veredicto = "WARN"

    return {
        "score_global": score_global,
        "veredicto": veredicto,
        "checks": checks,
        "problemas": problemas,
        "metricas": metricas,
        "nota_metodo": (
            "Evaluación autónoma de PDF: inspección directa de los bytes (PyMuPDF). "
            "Verifica integridad, conteo de imágenes embebidas y presencia de "
            "ambientes/campos en el texto. No valida el contenido visual (sin OCR/visión)."
        ),
    }
