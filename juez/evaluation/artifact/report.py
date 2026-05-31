"""Genera la seccion de texto del reporte de QA de artefactos."""
from __future__ import annotations

from typing import Any, Dict, List

_LINEA = "=" * 70


def generar_reporte_artefacto(agregado: Dict[str, Any]) -> str:
    """agregado = salida de run.run_artifact_eval()."""
    L: List[str] = []
    L.append(_LINEA)
    L.append("  QA DE ARTEFACTO (salida producida)")
    L.append(_LINEA)
    # ¿Se verifico realmente el contenido del artefacto, o solo el disparo?
    verificado = any(
        ev.get("metricas", {}).get("contenido_verificado")
        for ev in agregado.get("por_evaluador", [])
    )
    if verificado:
        L.append(f"  Score de artefacto : {agregado.get('score_artefacto', 0.0):.1f}/100")
    else:
        L.append(f"  Score de artefacto : {agregado.get('score_artefacto', 0.0):.1f}/100  "
                 "(PARCIAL - solo disparo; contenido del artefacto NO verificado)")

    for ev in agregado.get("por_evaluador", []):
        m = ev.get("metricas", {})
        L.append("")
        L.append(f"  -- {ev.get('nombre', 'evaluador')}  (score {ev.get('score', 0.0):.1f})")
        if "disparo_ok" in m:
            estado = "OK" if m["disparo_ok"] else "FALLO"
            L.append(f"     Disparo sintetico : {estado}  "
                     f"(HTTP {m.get('http_status')}, {m.get('latency_ms')} ms)")
        if "fotos_esperadas" in m or "fotos_embebidas" in m:
            L.append(f"     Fotos esperadas   : {m.get('fotos_esperadas', '?')}")
            L.append(f"     Fotos embebidas   : {m.get('fotos_embebidas', '?')}")
        if m.get("ambientes_faltantes"):
            L.append(f"     Ambientes faltantes: {', '.join(m['ambientes_faltantes'])}")
        if "paginas" in m:
            L.append(f"     Paginas PDF       : {m['paginas']}")
        if m.get("pdf_inspeccionado") is False:
            L.append("     PDF inspeccionado : NO (ver notas)")

    problemas = agregado.get("problemas", [])
    if problemas:
        L.append("")
        L.append("  Problemas detectados en el artefacto:")
        for p in problemas:
            L.append(f"     [{p.get('severidad')}] {p.get('descripcion')}")

    notas: List[str] = []
    for ev in agregado.get("por_evaluador", []):
        notas.extend(ev.get("metricas", {}).get("notas", []))
    if notas:
        L.append("")
        L.append("  Notas de infraestructura de QA (no penalizan al agente):")
        for n in notas:
            L.append(f"     - {n}")

    L.append(_LINEA)
    return "\n".join(L)
