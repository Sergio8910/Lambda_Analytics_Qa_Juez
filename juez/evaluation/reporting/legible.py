"""Render en lenguaje claro para usuarios NO técnicos.

Piezas reusables:
  - describir_plan_legible: explica en palabras simples QUÉ se va a evaluar (HU-16).
  - resumen_ejecutivo_txt: bloque ejecutivo (veredicto + qué significa + top
    puntos) para encabezar informes técnicos (HU-18).
  - render_informe_no_tecnico: informe UNICO dividido en 3 secciones (seguridad,
    funcionamiento, construccion tecnica) para un lector no tecnico -- agrupa,
    no filtra: todos los hallazgos aparecen, organizados por que significan.
"""
from __future__ import annotations

from typing import Any, Dict, List

_SEVERIDAD_LEGIBLE = {
    "critico": "Urgente", "critical": "Urgente",
    "alto": "Importante", "high": "Importante",
    "medio": "Moderado", "medium": "Moderado",
    "bajo": "Menor", "low": "Menor",
    "info": "Informativo",
}

_ORDEN_SEVERIDAD = {"critico": 0, "critical": 0, "alto": 1, "high": 1, "medio": 2, "medium": 2, "bajo": 3, "low": 3, "info": 4}

_SECCION_INFO = {
    "seguridad": (
        "SEGURIDAD",
        "Riesgos que podrian exponer datos sensibles, credenciales, o permitir accesos indebidos.",
    ),
    "funcional": (
        "FUNCIONAMIENTO",
        "Si el sistema realmente hace lo que se supone que debe hacer para el negocio.",
    ),
    "tecnico": (
        "CONSTRUCCION TECNICA",
        "Como esta armado por dentro: codigo, estructura, buenas practicas de mantenimiento.",
    ),
}

# Métricas internas → explicación en lenguaje claro.
_REGLA_LEGIBLE = {
    "answer_relevancy": "responde a lo que se le pregunta",
    "instruction_adherence": "sigue las instrucciones que se le dieron",
    "task_success": "logra completar la tarea del usuario",
    "faithfulness": "se basa en la información real, sin inventar",
    "contextual_precision": "usa el contexto correcto",
    "hallucination": "no inventa datos falsos",
    "completeness": "responde de forma completa",
    "format_compliance": "respeta el formato pedido",
    "latency_budget": "responde a tiempo",
    "refusal_quality": "rechaza bien lo que no debe hacer",
    "consistency": "es consistente",
    "tool_call_validity": "usa bien sus herramientas",
    "voice_quality": "suena bien en voz",
}

_VEREDICTO_LEGIBLE = {
    "excelente": "Excelente", "bueno": "Bien", "aceptable": "Aceptable",
    "deficiente": "Necesita mejoras", "critico": "Crítico — requiere atención",
    "cumple": "Cumple", "cumple_parcial": "Cumple a medias", "no_cumple": "No cumple",
    "ok": "Bien", "warning": "Con advertencias", "fail": "Con problemas",
}


def _veredicto(v: str) -> str:
    return _VEREDICTO_LEGIBLE.get((v or "").lower(), v or "—")


def describir_plan_legible(perfil: Dict[str, Any], reglas: List[Dict[str, Any]], n_casos: int) -> str:
    """Texto claro de QUÉ se va a evaluar, para un usuario no técnico."""
    L: List[str] = ["Esto es lo que se va a revisar del agente:\n"]
    dominio = perfil.get("domain") or "general"
    idioma = perfil.get("language") or "no detectado"
    L.append(f"• El agente parece ser del área de '{dominio}' y responde en '{idioma}'.")
    L.append(f"• Se le harán {n_casos} preguntas de prueba (casos reales y difíciles).")
    L.append("• Se revisará que:")
    for r in reglas:
        nombre = r.get("name", "")
        desc = _REGLA_LEGIBLE.get(nombre, nombre.replace("_", " "))
        umbral = r.get("umbral", r.get("threshold"))
        exig = f" (exigencia {int(float(umbral) * 100)}%)" if umbral is not None else ""
        L.append(f"    - {desc}{exig}.")
    L.append("\nPuedes ajustar la exigencia de cada punto o quitar los que no apliquen antes de evaluar.")
    return "\n".join(L)


def resumen_ejecutivo_txt(
    titulo: str, veredicto: str, score: Any, problemas: List[Dict[str, Any]], que_se_evaluo: str = ""
) -> str:
    """Bloque ejecutivo (no técnico) para encabezar un informe."""
    L = ["=" * 70, "  RESUMEN EJECUTIVO (para todos)", "=" * 70]
    L.append(f"  {titulo}")
    L.append(f"  Resultado: {_veredicto(veredicto)}" + (f"  ({score}/100)" if score is not None else ""))
    if que_se_evaluo:
        L.append(f"  Qué se evaluó: {que_se_evaluo}")

    # Top problemas en lenguaje simple (los más graves primero)
    orden = {"critico": 0, "critical": 0, "alto": 1, "high": 1, "medio": 2, "medium": 2}
    graves = sorted(problemas, key=lambda p: orden.get(str(p.get("severidad", p.get("severity", ""))).lower(), 9))
    if graves:
        L.append("")
        L.append("  Principales puntos a mejorar:")
        for p in graves[:3]:
            desc = p.get("descripcion") or p.get("message") or p.get("title") or ""
            L.append(f"    • {desc}")
    else:
        L.append("")
        L.append("  Sin problemas relevantes detectados.")
    L.append("=" * 70)
    return "\n".join(L)


def render_informe_no_tecnico(
    titulo: str,
    veredicto: str,
    score: Any,
    problemas: List[Dict[str, Any]],
    que_se_evaluo: str = "",
) -> str:
    """Informe UNICO para un lector no tecnico, dividido en 3 secciones
    (seguridad / funcionamiento / construccion tecnica). Agrupa TODOS los
    hallazgos -- ninguno se descarta, solo se organizan por lo que significan
    para que alguien sin background tecnico entienda de un vistazo que tan
    grave es cada cosa y en que area cae.
    """
    from ..qa_mode import SECCIONES, agrupar_por_seccion

    L = [resumen_ejecutivo_txt(titulo, veredicto, score, problemas, que_se_evaluo)]
    secciones = agrupar_por_seccion(problemas)

    for clave in SECCIONES:
        items = secciones.get(clave, [])
        nombre, explicacion = _SECCION_INFO[clave]
        L.append("")
        L.append("=" * 70)
        L.append(f"  {nombre} ({len(items)})")
        L.append("=" * 70)
        L.append(f"  {explicacion}")
        L.append("")
        if not items:
            L.append("  Sin hallazgos en esta seccion.")
            continue
        ordenados = sorted(
            items, key=lambda p: _ORDEN_SEVERIDAD.get(str(p.get("severidad", p.get("severity", ""))).lower(), 9)
        )
        for p in ordenados:
            sev_cruda = str(p.get("severidad", p.get("severity", ""))).lower()
            sev = _SEVERIDAD_LEGIBLE.get(sev_cruda, p.get("severidad", p.get("severity", "")))
            desc = p.get("descripcion") or p.get("message") or p.get("title") or ""
            L.append(f"  [{sev}] {desc}")
            donde = p.get("nodo") or p.get("file")
            if donde:
                L.append(f"      Donde: {donde}")

    L.append("")
    L.append("=" * 70)
    return "\n".join(L)
