"""Análisis de coherencia del pipeline multi-agente — usa GPT-4o para evaluar
si los agentes del pipeline son compatibles entre sí y si el flujo tiene sentido."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

_MODEL = "gpt-4o"

# Latencia base por tipo de nodo (ms)
_LATENCY_BY_TYPE: Dict[str, int] = {
    "elevenlabs": 800,
    "n8n": 500,
    "openai": 600,
    "anthropic": 600,
    "webhook": 200,
    "http": 300,
    "database": 150,
    "default": 400,
}


def _latencia_por_tipo(node_type: str) -> int:
    """Retorna latencia estimada en ms según el tipo de nodo."""
    key = (node_type or "default").lower()
    for prefix, ms in _LATENCY_BY_TYPE.items():
        if prefix in key:
            return ms
    return _LATENCY_BY_TYPE["default"]


def _normalizar_edges(raw_edges: List[Any]) -> List[Dict[str, Any]]:
    """Normaliza una lista de edges que puede ser dicts o dataclasses PipelineEdge."""
    result: List[Dict[str, Any]] = []
    for e in raw_edges:
        if isinstance(e, dict):
            result.append(e)
        else:
            result.append({
                "source": getattr(e, "source_id", getattr(e, "source", "")),
                "target": getattr(e, "target_id", getattr(e, "target", "")),
                "match_type": getattr(e, "match_type", ""),
            })
    return result


def _normalizar_gaps(raw_gaps: List[Any]) -> List[Dict[str, Any]]:
    """Normaliza gaps que puede ser dicts o dataclasses PipelineGap."""
    result: List[Dict[str, Any]] = []
    for g in raw_gaps:
        if isinstance(g, dict):
            result.append(g)
        else:
            result.append({
                "node_id":     getattr(g, "node_id", ""),
                "exit_url":    getattr(g, "exit_url", ""),
                "description": getattr(g, "description", ""),
                "caller":      getattr(g, "node_id", ""),
                "endpoint":    getattr(g, "exit_url", ""),
            })
    return result


def _calcular_heuristicas(
    nodes_data: List[Dict[str, Any]],
    edges: List[Any],
    order: List[str],
    gaps: List[Any],
) -> Dict[str, Any]:
    """Calcula métricas heurísticas sin llamada a API."""
    # Normalizar edges y gaps (acepta dicts o dataclasses)
    edges = _normalizar_edges(edges)
    gaps = _normalizar_gaps(gaps)

    # Resolver caller en gaps: si llegó como node_id normalizado (e.g. "euro_-_contact_center")
    # intentar mapear al nombre legible usando los nodes_data disponibles.
    # Esto cubre el camino directo (cuando los gaps llegan como dataclasses PipelineGap)
    # porque _normalizar_gaps() solo tiene el node_id, no el nombre.
    _name_map = {}
    for _n in nodes_data:
        _friendly = _n.get("name", "")
        if _friendly:
            # La misma normalización que usa evaluar_pipeline.py al construir node_id
            _norm = _friendly.lower().replace(" ", "_")[:40]
            _name_map[_norm] = _friendly
    for _gap in gaps:
        _raw_caller = _gap.get("caller", "")
        if _raw_caller and _raw_caller in _name_map:
            # El caller era un ID normalizado — reemplazar con nombre legible
            _gap["caller"] = _name_map[_raw_caller]

    # --- punto más débil ---
    punto_mas_debil = ""
    min_score = 101.0
    for node in nodes_data:
        s = node.get("scores", {})
        sg = s.get("score_general", s.get("overall_score", 100.0))
        if sg is not None and sg < min_score:
            min_score = float(sg)
            punto_mas_debil = node.get("name", "desconocido")

    # --- latencia estimada ---
    latencia_ms = 0
    for node in nodes_data:
        s = node.get("scores", {})
        lat = s.get("latencia_ms", s.get("latency_ms"))
        if lat is not None:
            latencia_ms += int(lat)
        else:
            latencia_ms += _latencia_por_tipo(node.get("node_type", ""))

    # --- detección de ciclos (DFS simple) ---
    adj: Dict[str, List[str]] = {}
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        adj.setdefault(src, []).append(tgt)

    ciclos: List[str] = []
    visited: set = set()
    rec_stack: set = set()

    def _dfs_cycle(node_id: str) -> bool:
        visited.add(node_id)
        rec_stack.add(node_id)
        for neighbor in adj.get(node_id, []):
            if neighbor not in visited:
                if _dfs_cycle(neighbor):
                    return True
            elif neighbor in rec_stack:
                ciclos.append(f"{node_id} → {neighbor}")
                return True
        rec_stack.discard(node_id)
        return False

    all_nodes = {n.get("name", str(i)) for i, n in enumerate(nodes_data)}
    for node_id in adj:
        if node_id not in visited:
            _dfs_cycle(node_id)

    # --- score de coherencia heurístico ---
    scores_individuales = []
    for node in nodes_data:
        s = node.get("scores", {})
        sg = s.get("score_general", s.get("overall_score"))
        if sg is not None:
            scores_individuales.append(float(sg))

    if scores_individuales:
        score_base = sum(scores_individuales) / len(scores_individuales)
    else:
        score_base = 75.0

    penalizacion = len(gaps) * 10 + len(ciclos) * 20
    score_coherencia = max(0.0, min(100.0, score_base - penalizacion))

    # --- riesgos heurísticos ---
    riesgos: List[str] = []
    for gap in gaps:
        caller = gap.get("caller", gap.get("source", "nodo desconocido"))
        endpoint = gap.get("endpoint", gap.get("target", "destino desconocido"))
        riesgos.append(
            f'Gap sin receptor: "{caller}" llama {endpoint} — ningún nodo responde'
        )

    for node in nodes_data:
        analisis = node.get("analisis", {})
        problemas = analisis.get("problemas", [])
        if isinstance(problemas, list):
            for p in problemas:
                tipo = p.get("tipo", "") if isinstance(p, dict) else str(p)
                if "resiliencia" in tipo.lower() or "retry" in tipo.lower():
                    riesgos.append(
                        f'Nodo "{node.get("name", "?")} " sin mecanismo de retry/resiliencia'
                    )
                    break

    for ciclo in ciclos:
        riesgos.append(f"Ciclo detectado en el flujo: {ciclo}")

    return {
        "score_coherencia": round(score_coherencia, 1),
        "resumen": (
            f"Pipeline de {len(nodes_data)} nodo(s). "
            f"Score promedio individual: {round(score_base, 1)}%. "
            f"Gaps detectados: {len(gaps)}. Ciclos: {len(ciclos)}."
        ),
        "riesgos": riesgos,
        "recomendaciones": _recomendaciones_heuristicas(nodes_data, gaps, ciclos),
        "compatibilidad_datos": (
            "Análisis heurístico: se recomienda verificar manualmente "
            "que los outputs de cada nodo coincidan con los inputs esperados por el siguiente."
        ),
        "latencia_estimada_ms": latencia_ms,
        "punto_mas_debil": punto_mas_debil,
        "_ciclos_detectados": ciclos,
    }


def _recomendaciones_heuristicas(
    nodes_data: List[Dict[str, Any]],
    gaps: List[Dict],
    ciclos: List[str],
) -> List[str]:
    """Genera recomendaciones básicas a partir de las métricas disponibles."""
    recs: List[str] = []

    if gaps:
        recs.append(
            "Revisar y corregir los gaps detectados: algunos endpoints llamados "
            "no tienen un nodo receptor en el pipeline."
        )

    if ciclos:
        recs.append(
            "Eliminar o refactorizar los ciclos detectados para evitar "
            "bucles infinitos en producción."
        )

    # Nodos con score bajo
    for node in nodes_data:
        s = node.get("scores", {})
        sg = s.get("score_general", s.get("overall_score"))
        if sg is not None and float(sg) < 70.0:
            recs.append(
                f'Priorizar mejoras en el nodo "{node.get("name", "?")}" '
                f"(score {round(float(sg), 1)}%) antes de pasar a producción."
            )

    if not recs:
        recs.append(
            "El pipeline presenta buena coherencia general. "
            "Continuar con pruebas de integración end-to-end."
        )

    return recs


def _llamar_gpt4o(
    nodes_data: List[Dict[str, Any]],
    edges: List[Dict],
    order: List[str],
    gaps: List[Dict],
    heuristicas: Dict[str, Any],
    openai_key: str,
) -> Dict[str, Any]:
    """Llama a GPT-4o para enriquecer el análisis heurístico."""
    try:
        import openai  # type: ignore
    except ImportError:
        return {}

    # Construir resumen compacto del pipeline
    nodos_resumen = []
    for n in nodes_data:
        s = n.get("scores", {})
        sg = s.get("score_general", s.get("overall_score", "N/A"))
        nodos_resumen.append(
            {
                "name": n.get("name"),
                "type": n.get("node_type"),
                "score_general": sg,
                "problemas": n.get("analisis", {}).get("problemas", []),
            }
        )

    conexiones_resumen = [
        {
            "de": e.get("source"),
            "hacia": e.get("target"),
            "tipo": e.get("match_type", e.get("type", "desconocido")),
        }
        for e in edges
    ]

    pipeline_summary = {
        "orden_topologico": order,
        "nodos": nodos_resumen,
        "conexiones": conexiones_resumen,
        "gaps_detectados": gaps,
        "score_coherencia_heuristico": heuristicas.get("score_coherencia"),
        "riesgos_heuristicos": heuristicas.get("riesgos", []),
    }

    prompt_sistema = (
        "Eres un arquitecto de sistemas experto en pipelines de agentes LLM e integraciones. "
        "Analiza el pipeline multi-agente descrito y responde EXCLUSIVAMENTE con un JSON válido "
        "(sin markdown, sin texto adicional) con las claves: "
        "score_coherencia (float 0-100), resumen (string), riesgos (list of strings), "
        "recomendaciones (list of strings), compatibilidad_datos (string)."
    )

    prompt_usuario = (
        "Analiza este pipeline multi-agente y evalúa:\n"
        "1. Compatibilidad de datos: ¿los outputs de cada nodo son inputs válidos del siguiente?\n"
        "2. Riesgos no obvios (seguridad, latencia, consistencia, manejo de errores).\n"
        "3. Recomendaciones específicas y accionables.\n"
        "4. Un score de coherencia refinado (0-100).\n\n"
        f"Pipeline:\n{json.dumps(pipeline_summary, ensure_ascii=False, indent=2)}"
    )

    client = openai.OpenAI(api_key=openai_key)
    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_usuario},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        # Limpiar posibles bloques markdown
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content.strip())
        return parsed
    except Exception:
        return {}


def analizar_coherencia_pipeline(
    nodes_data: List[Dict[str, Any]],
    edges: List[Dict],
    order: List[str],
    gaps: List[Dict],
    openai_key: str = "",
) -> Dict[str, Any]:
    """Analiza el pipeline completo y retorna un dict con:
    {
        "score_coherencia": float (0-100),
        "resumen": str,
        "riesgos": List[str],
        "recomendaciones": List[str],
        "compatibilidad_datos": str,  # descripción de si los outputs de A son inputs válidos de B
        "latencia_estimada_ms": int,
        "punto_mas_debil": str,   # nombre del nodo con menor score individual
    }
    """
    # 1. Siempre calcular heurísticas
    resultado = _calcular_heuristicas(nodes_data, edges, order, gaps)

    # 2. Enriquecer con GPT-4o si hay API key
    api_key = openai_key or os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        gpt_result = _llamar_gpt4o(
            nodes_data, edges, order, gaps, resultado, api_key
        )
        if gpt_result:
            # Fusionar: GPT-4o refina score, resumen, riesgos y recomendaciones
            if "score_coherencia" in gpt_result:
                try:
                    resultado["score_coherencia"] = float(
                        gpt_result["score_coherencia"]
                    )
                except (ValueError, TypeError):
                    pass
            if "resumen" in gpt_result and gpt_result["resumen"]:
                resultado["resumen"] = str(gpt_result["resumen"])
            if "riesgos" in gpt_result and isinstance(
                gpt_result["riesgos"], list
            ):
                # Unir riesgos heurísticos únicos + los de GPT-4o
                vistos = set(resultado["riesgos"])
                for r in gpt_result["riesgos"]:
                    if r not in vistos:
                        resultado["riesgos"].append(r)
                        vistos.add(r)
            if "recomendaciones" in gpt_result and isinstance(
                gpt_result["recomendaciones"], list
            ):
                vistos = set(resultado["recomendaciones"])
                for r in gpt_result["recomendaciones"]:
                    if r not in vistos:
                        resultado["recomendaciones"].append(r)
                        vistos.add(r)
            if "compatibilidad_datos" in gpt_result and gpt_result[
                "compatibilidad_datos"
            ]:
                resultado["compatibilidad_datos"] = str(
                    gpt_result["compatibilidad_datos"]
                )

    # Limpiar clave interna antes de retornar
    resultado.pop("_ciclos_detectados", None)

    return {
        "score_coherencia": resultado.get("score_coherencia", 0.0),
        "resumen": resultado.get("resumen", ""),
        "riesgos": resultado.get("riesgos", []),
        "recomendaciones": resultado.get("recomendaciones", []),
        "compatibilidad_datos": resultado.get("compatibilidad_datos", ""),
        "latencia_estimada_ms": resultado.get("latencia_estimada_ms", 0),
        "punto_mas_debil": resultado.get("punto_mas_debil", ""),
    }
