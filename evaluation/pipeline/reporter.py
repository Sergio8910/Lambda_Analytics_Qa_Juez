"""Generación del reporte completo del pipeline multi-agente en texto plano."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Utilidades visuales
# ---------------------------------------------------------------------------

def _barra(pct: Optional[float], width: int = 10) -> str:
    """Retorna una barra visual de `width` chars usando bloques Unicode."""
    if pct is None:
        return "░" * width
    filled = round(float(pct) / 100 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _nivel(pct: Optional[float]) -> str:
    """Retorna etiqueta de nivel según el porcentaje."""
    if pct is None:
        return "SIN DATOS"
    v = float(pct)
    if v >= 90:
        return "EXCELENTE"
    if v >= 75:
        return "BUENO"
    if v >= 60:
        return "ACEPTABLE"
    if v >= 40:
        return "DEFICIENTE"
    return "CRITICO"


def _icono_nivel(pct: Optional[float]) -> str:
    """Icono de estado basado en nivel."""
    if pct is None:
        return "?"
    if float(pct) >= 75:
        return "OK"
    return "FALLO"


def _score_str(pct: Optional[float]) -> str:
    """Formatea el score como porcentaje."""
    if pct is None:
        return "  N/A "
    return f"{float(pct):5.1f}%"


# ---------------------------------------------------------------------------
# Diagrama ASCII del pipeline
# ---------------------------------------------------------------------------

def _tipo_conexion(edge: Dict) -> str:
    """Retorna la etiqueta de la conexión (webhook, flow, etc.)."""
    mt = edge.get("match_type", edge.get("type", "")).lower()
    if "webhook" in mt:
        return "webhook"
    if "flow" in mt:
        return "flow"
    if "http" in mt:
        return "http"
    return mt or "link"


def _diagrama_ascii(graph: Any) -> str:
    """Genera diagrama de flujo ASCII a partir del PipelineGraph.

    Espera que `graph` tenga:
      - graph.nodes: dict[id, {name, node_type, scores, ...}]
      - graph.edges: list[{source, target, match_type/type}]
      - graph.topological_order(): list[id]  (opcional, fallback a nodes.keys())
      - graph.gaps: list[dict]  (opcional)
      - graph.cycles: list[str]  (opcional)
    """
    # --- extraer datos con duck typing ---
    nodes: Dict[str, Any] = {}
    edges: List[Dict] = []
    order: List[str] = []
    gaps: List[Dict] = []
    cycles: List[str] = []

    if hasattr(graph, "nodes"):
        raw_nodes = graph.nodes
        raw_dict = dict(raw_nodes) if not isinstance(raw_nodes, dict) else raw_nodes
        # Normalizar: acepta PipelineNode dataclasses o dicts
        nodes = {}
        for nid, nd in raw_dict.items():
            if isinstance(nd, dict):
                nodes[nid] = nd
            else:
                nodes[nid] = {
                    "name":      getattr(nd, "name", nid),
                    "node_type": getattr(nd, "node_type", ""),
                    "scores":    getattr(nd, "scores", {}),
                }
    if hasattr(graph, "edges"):
        # Normalizar: acepta tanto dicts como dataclasses (PipelineEdge)
        raw_edges = list(graph.edges)
        edges = []
        for e in raw_edges:
            if isinstance(e, dict):
                edges.append(e)
            else:
                edges.append({
                    "source": getattr(e, "source_id", getattr(e, "source", "")),
                    "target": getattr(e, "target_id", getattr(e, "target", "")),
                    "match_type": getattr(e, "match_type", ""),
                })

    # Orden topológico: usar graph.order (atributo) o graph.topological_order() (método)
    if hasattr(graph, "order") and graph.order:
        order = list(graph.order)
    elif hasattr(graph, "topological_order"):
        try:
            order = list(graph.topological_order())
        except Exception:
            order = list(nodes.keys())
    else:
        order = list(nodes.keys())
    if hasattr(graph, "gaps"):
        raw_gaps = list(graph.gaps or [])
        gaps = []
        for g in raw_gaps:
            if isinstance(g, dict):
                gaps.append(g)
            else:
                gaps.append({
                    "node_id":   getattr(g, "node_id", ""),
                    "exit_url":  getattr(g, "exit_url", ""),
                    "description": getattr(g, "description", ""),
                })
    if hasattr(graph, "cycles"):
        cycles = list(graph.cycles or [])

    if not nodes:
        return "  (sin nodos)"

    # Mapas de conveniencia
    edge_map: Dict[str, List[Dict]] = {}  # source_id -> [edge, ...]
    for e in edges:
        src = e.get("source", "")
        edge_map.setdefault(src, []).append(e)

    # nombre corto para mostrar
    def _label(node_id: str) -> str:
        nd = nodes.get(node_id, {})
        name = nd.get("name", node_id)
        nt = nd.get("node_type", "")
        if nt:
            return f"{name} ({nt})"
        return name

    def _score_node(node_id: str) -> Optional[float]:
        nd = nodes.get(node_id, {})
        s = nd.get("scores", {})
        return s.get("score_general", s.get("overall_score"))

    lines: List[str] = []
    use_vertical = len(nodes) > 4

    if use_vertical:
        # Formato vertical
        for idx, nid in enumerate(order, 1):
            nd = nodes.get(nid, {})
            name = nd.get("name", nid)
            nt = nd.get("node_type", "")
            sc = _score_node(nid)
            tipo_str = f" ({nt})" if nt else ""
            score_tag = f"  {_score_str(sc)}" if sc is not None else ""
            lines.append(f"  [{idx}] {name}{tipo_str}{score_tag}")
            # aristas salientes desde este nodo
            for e in edge_map.get(nid, []):
                conn = _tipo_conexion(e)
                lines.append(f"       │ {conn}")
                lines.append("       ▼")
    else:
        # Formato horizontal (hasta 4 nodos)
        node_labels = []
        node_scores = []
        node_status = []
        for nid in order:
            nd = nodes.get(nid, {})
            name = nd.get("name", nid)
            nt = nd.get("node_type", "")
            sc = _score_node(nid)
            lbl = f"[{nt}] {name}" if nt else name
            node_labels.append(lbl)
            node_scores.append(_score_str(sc) if sc is not None else " N/A ")
            status = ("OK" if sc is not None and float(sc) >= 75 else "FALLO") if sc is not None else "?"
            node_status.append(("+" if status == "OK" else "x") + " " + status)

        # Construir fila de nodos
        parts_top: List[str] = []
        parts_mid: List[str] = []
        parts_bot: List[str] = []

        for idx, (lbl, sc_s, st) in enumerate(
            zip(node_labels, node_scores, node_status)
        ):
            box = f"  {lbl}  "
            w = max(len(box), len(sc_s) + 4, len(st) + 4)
            parts_top.append(box.center(w))
            parts_mid.append(sc_s.center(w))
            parts_bot.append(st.center(w))

        # Separadores de conexión
        connectors: List[str] = []
        for idx in range(len(order) - 1):
            src_id = order[idx]
            conn_label = ""
            for e in edge_map.get(src_id, []):
                if e.get("target") == order[idx + 1]:
                    conn_label = _tipo_conexion(e)
                    break
            if not conn_label and edge_map.get(src_id):
                conn_label = _tipo_conexion(edge_map[src_id][0])
            connectors.append(f" ──{conn_label}──► ")

        # Ensamblar fila
        row_top = ""
        row_mid = ""
        row_bot = ""
        for i, (top, mid, bot) in enumerate(zip(parts_top, parts_mid, parts_bot)):
            row_top += top
            row_mid += mid
            row_bot += bot
            if i < len(connectors):
                row_top += connectors[i]
                row_mid += " " * len(connectors[i])
                row_bot += " " * len(connectors[i])

        lines.append(row_top)
        lines.append(row_mid)
        lines.append(row_bot)

    # Gaps
    if gaps:
        lines.append("")
        lines.append("--- GAPS DETECTADOS " + "-" * 49)
        for gap in gaps:
            caller = gap.get("caller", gap.get("source", "nodo"))
            endpoint = gap.get("endpoint", gap.get("target", "endpoint"))
            lines.append(
                f'  x "{caller}" llama {endpoint} — ningún nodo del pipeline responde'
            )

    # Ciclos
    lines.append("")
    lines.append("--- CICLOS " + "-" * 58)
    if cycles:
        for c in cycles:
            lines.append(f"  ! {c}")
    else:
        lines.append("  (ninguno)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scorecard del pipeline
# ---------------------------------------------------------------------------

def _seccion_scorecard(
    analisis_pipeline: Dict[str, Any],
    agent_results: List[Dict],
    punto_mas_debil: str,
) -> str:
    """Genera la sección SCORECARD del reporte."""
    sc_coherencia = analisis_pipeline.get("score_coherencia")
    # Calcular resiliencia promedio si está disponible
    resiliences = []
    scores_ind = []
    for ar in agent_results:
        s = ar.get("scores", {})
        sg = s.get("score_general", s.get("overall_score"))
        if sg is not None:
            scores_ind.append(float(sg))
        res = s.get("resiliencia", s.get("resilience"))
        if res is not None:
            resiliences.append(float(res))

    sc_resiliencia: Optional[float] = (
        sum(resiliences) / len(resiliences) if resiliences else None
    )
    sc_promedio: Optional[float] = (
        sum(scores_ind) / len(scores_ind) if scores_ind else None
    )

    # Score pipeline = promedio ponderado
    candidates = [v for v in [sc_coherencia, sc_resiliencia, sc_promedio] if v is not None]
    sc_pipeline: Optional[float] = (
        sum(candidates) / len(candidates) if candidates else None
    )

    lines: List[str] = []
    SEP = "=" * 80

    def _linea_score(label: str, pct: Optional[float], extra: str = "") -> str:
        bar = _barra(pct)
        nivel = _nivel(pct)
        sc = _score_str(pct)
        base = f"  {label:<36}{sc}  [{bar}]  {nivel}"
        return base + (f"  {extra}" if extra else "")

    # Nodos que solo tuvieron análisis estático (sin pruebas dinámicas)
    nodos_solo_estatico: List[str] = analisis_pipeline.get("nodos_solo_estatico", [])

    lines.append(f"  SCORE PIPELINE     {_score_str(sc_pipeline)}  [{_barra(sc_pipeline)}]  {_nivel(sc_pipeline)}")
    lines.append("")
    lines.append("  DIMENSIONES:")
    lines.append(_linea_score("Coherencia", sc_coherencia))
    if sc_resiliencia is not None:
        lines.append(_linea_score("Resiliencia", sc_resiliencia))
    if sc_promedio is not None:
        lines.append(_linea_score("Score promedio individual", sc_promedio))

    # Advertencia de nodos solo estáticos — antes de la tabla de nodos
    if nodos_solo_estatico:
        lines.append("")
        lines.append("  ADVERTENCIA — SCORE PARCIAL:")
        lines.append(
            f"  {len(nodos_solo_estatico)} nodo(s) evaluado(s) SOLO con analisis estatico"
            " (sin pruebas dinamicas)."
        )
        lines.append(
            "  El score de estos nodos refleja unicamente estructura, seguridad y"
        )
        lines.append(
            "  prompts — NO mide comportamiento real. Activa el webhook en n8n"
        )
        lines.append(
            "  para obtener el score completo con pruebas conversacionales."
        )
        for n in nodos_solo_estatico:
            lines.append(f"    * {n}")

    lines.append("")
    lines.append("  NODOS (score individual):")
    for idx, ar in enumerate(agent_results, 1):
        name = ar.get("name", f"Nodo {idx}")
        nt = ar.get("node_type", "")
        s = ar.get("scores", {})
        sg = s.get("score_general", s.get("overall_score"))
        tag_debil = "  <- punto mas debil" if name == punto_mas_debil else ""
        tipo_str = f" ({nt})" if nt else ""
        # Etiquetar como solo estático si aplica
        tag_estatico = "  [solo estatico]" if name in nodos_solo_estatico else ""
        lines.append(
            f"  > [{idx}] {name}{tipo_str:<28} {_score_str(sg)}  [{_barra(sg)}]  {_nivel(sg)}{tag_debil}{tag_estatico}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reporte completo
# ---------------------------------------------------------------------------

def generar_reporte_pipeline(
    graph: Any,
    analisis_pipeline: Dict[str, Any],
    agent_results: List[Dict],
    pipeline_name: str = "Pipeline",
) -> str:
    """Genera el reporte completo del pipeline en texto plano."""
    SEP = "=" * 80
    SEP_THIN = "-" * 80
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_nodos = len(agent_results)
    punto_mas_debil = analisis_pipeline.get("punto_mas_debil", "")

    sections: List[str] = []

    # ---- ENCABEZADO ----
    sections.append(SEP)
    sections.append(
        "         LAMBDA ANALYTICS JUEZ -- EVALUACION DE PIPELINE MULTI-AGENTE"
    )
    sections.append(SEP)
    sections.append("")
    sections.append(f"Pipeline    : {pipeline_name}")
    sections.append(f"Nodos       : {n_nodos} agentes/flujos")
    sections.append(f"Fecha       : {timestamp}")
    sections.append("")

    # ---- DIAGRAMA ----
    sections.append(SEP)
    sections.append("                           DIAGRAMA DEL PIPELINE")
    sections.append(SEP)
    sections.append("")
    sections.append(_diagrama_ascii(graph))
    sections.append("")

    # ---- SCORECARD ----
    sections.append(SEP)
    sections.append("                         SCORECARD DEL PIPELINE")
    sections.append(SEP)
    sections.append("")
    sections.append(
        _seccion_scorecard(analisis_pipeline, agent_results, punto_mas_debil)
    )
    sections.append("")

    # ---- ANÁLISIS DE COHERENCIA ----
    sections.append("--- ANALISIS DE COHERENCIA " + "-" * 53)
    resumen = analisis_pipeline.get("resumen", "")
    if resumen:
        # Wrap manual a 76 chars
        words = resumen.split()
        line_buf = "  "
        for w in words:
            if len(line_buf) + len(w) + 1 > 78:
                sections.append(line_buf)
                line_buf = "  " + w
            else:
                line_buf += (" " if line_buf.strip() else "") + w
        if line_buf.strip():
            sections.append(line_buf)
    sections.append("")

    # ---- COMPATIBILIDAD DE DATOS ----
    compat = analisis_pipeline.get("compatibilidad_datos", "")
    if compat:
        sections.append("--- COMPATIBILIDAD DE DATOS " + "-" * 52)
        words = compat.split()
        line_buf = "  "
        for w in words:
            if len(line_buf) + len(w) + 1 > 78:
                sections.append(line_buf)
                line_buf = "  " + w
            else:
                line_buf += (" " if line_buf.strip() else "") + w
        if line_buf.strip():
            sections.append(line_buf)
        sections.append("")

    # ---- LATENCIA ----
    lat_ms = analisis_pipeline.get("latencia_estimada_ms")
    if lat_ms is not None and int(lat_ms) > 0:
        sections.append("--- LATENCIA ESTIMADA " + "-" * 57)
        sections.append(f"  {int(lat_ms)} ms totales (suma de nodos)")
        sections.append("")

    # ---- RIESGOS ----
    riesgos = analisis_pipeline.get("riesgos", [])
    sections.append("--- RIESGOS DETECTADOS " + "-" * 56)
    if riesgos:
        for i, r in enumerate(riesgos, 1):
            sections.append(f"  [{i}] {r}")
    else:
        sections.append("  (ninguno detectado)")
    sections.append("")

    # ---- RECOMENDACIONES ----
    recs = analisis_pipeline.get("recomendaciones", [])
    sections.append("--- RECOMENDACIONES " + "-" * 59)
    if recs:
        for i, r in enumerate(recs, 1):
            sections.append(f"  [{i}] {r}")
    else:
        sections.append("  (sin recomendaciones adicionales)")
    sections.append("")

    # ---- REPORTES INDIVIDUALES ----
    sections.append(SEP)
    sections.append("          REPORTES INDIVIDUALES (ver detalle de cada nodo abajo)")
    sections.append(SEP)
    sections.append("")

    for ar in agent_results:
        name = ar.get("name", "Nodo")
        nt = ar.get("node_type", "")
        tipo_str = f" [{nt}]" if nt else ""
        sections.append(f"=== NODO: {name}{tipo_str} " + "=" * max(0, 69 - len(name) - len(tipo_str)))
        sections.append("")
        reporte_texto = ar.get("reporte_texto", "")
        if reporte_texto:
            sections.append(reporte_texto)
        else:
            # Fallback: mostrar scores disponibles
            s = ar.get("scores", {})
            if s:
                for k, v in s.items():
                    if v is not None:
                        try:
                            sections.append(f"  {k}: {float(v):.1f}%")
                        except (ValueError, TypeError):
                            sections.append(f"  {k}: {v}")
            else:
                sections.append("  (sin reporte disponible)")
        sections.append("")

    return "\n".join(sections)
