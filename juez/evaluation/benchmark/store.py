"""Benchmark global de agentes — acumula datos entre evaluaciones y calcula estadísticas.

El benchmark permite contextualizar el score de un agente contra el promedio
de todos los agentes evaluados con el Juez. Los datos se persisten en
outputs/benchmark/global.json.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


_BENCHMARK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "outputs", "benchmark", "global.json"
)

_DOMAIN_NORMALIZATION: List[tuple] = [
    (["contact center", "call center", "centro de contacto", "contact centre"], "Contact Center"),
    (["delivery", "domicilios", "logistica", "logística", "envios", "envíos", "mensajeria", "mensajería"], "Delivery & Logística"),
    (["seguros", "insurance", "cobertura", "aseguradora", "poliza", "póliza"], "Seguros"),
    (["salud", "health", "medico", "médico", "clinica", "clínica", "hospital", "farmacia"], "Salud"),
    (["reservas", "hotel", "hospitality", "hospitalidad", "alojamiento", "turismo"], "Hospitalidad"),
    (["ventas", "sales", "comercial", "cotizacion", "cotización"], "Ventas"),
    (["ecommerce", "e-commerce", "tienda", "compras", "marketplace"], "E-commerce"),
    (["soporte", "support", "tecnico", "técnico", "helpdesk", "mesa de ayuda"], "Soporte Técnico"),
    (["banco", "banca", "financiero", "financiera", "credito", "crédito", "pagos"], "Banca & Finanzas"),
]


def normalizar_dominio(domain: str) -> str:
    """Mapea un dominio libre a una categoría canónica."""
    if not domain:
        return "Otro"
    lower = domain.lower().strip()
    for keywords, canonical in _DOMAIN_NORMALIZATION:
        if any(kw in lower for kw in keywords):
            return canonical
    # Si no hay match, titulizar el dominio original (máximo 30 chars)
    return domain.strip().title()[:30] or "Otro"


# Versión actual del esquema. Entradas con juez_version distinto no entran al
# promedio de benchmark — los pesos y mapeos cambian entre versiones y mezclar
# distorsiona la comparación.
JUEZ_VERSION = 2

_DIMENSION_LABELS = {
    "seguridad":           "Seguridad",
    "tools_integraciones": "Tools & Webhooks",
    "observabilidad":      "Observabilidad",
    "calidad_prompt":      "Calidad del Prompt",
    "mantenibilidad":      "Mantenibilidad",
    "artefacto":           "QA de Artefacto",
    "evaluacion_viva":     "Evaluacion en Vivo",
    "config_voz":          "Config. de Voz",
}

_CATEGORY_LABELS = {
    "happy_path":       "Happy Path",
    "herramienta":      "Herramienta",
    "multi_turno":      "Multi-turno",
    "limite":           "Limite de Dominio",
    "caos":             "Resistencia al Caos",
    "agresivo":         "Manejo Agresivo",
    "seguridad":        "Seguridad",
    "contexto_multiple":"Contexto Multiple",
}


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(_BENCHMARK_PATH), exist_ok=True)


def _cargar() -> Dict[str, Any]:
    if not os.path.exists(_BENCHMARK_PATH):
        return {"entries": []}
    try:
        with open(_BENCHMARK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entries": []}


def _guardar(data: Dict[str, Any]) -> None:
    _ensure_dir()
    with open(_BENCHMARK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guardar_entrada(
    agent_id: str,
    agent_name: str,
    domain: str,
    scores: Dict[str, Any],
) -> None:
    """Agrega una entrada al benchmark global.

    scores debe tener las mismas claves que build_snapshot retorna:
    score_general, calidad_prompt, config_voz, tools_integraciones,
    seguridad, observabilidad, evaluacion_viva, por_categoria (dict cat->pct).
    """
    entry: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "juez_version": scores.get("juez_version", JUEZ_VERSION),
        "agent_id": agent_id,
        "agent_name": agent_name,
        "domain": normalizar_dominio(domain),
        "score_general": round(scores.get("score_general", 0.0), 1),
        "dimensiones": {
            k: round(scores.get(k, 0.0) or 0.0, 1)
            for k in _DIMENSION_LABELS
        },
        "por_categoria": {
            cat: round(pct, 1)
            for cat, pct in (scores.get("por_categoria") or {}).items()
        },
    }
    data = _cargar()
    entries = data.setdefault("entries", [])
    # Upsert: reemplaza la entrada del mismo agente si ya existe
    idx = next((i for i, e in enumerate(entries) if e.get("agent_id") == agent_id), None)
    if idx is not None:
        entries[idx] = entry
    else:
        entries.append(entry)
    _guardar(data)


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _percentil(score: float, valores: List[float]) -> Optional[int]:
    """Percentil de score dentro de valores (cuántos están por debajo)."""
    if not valores:
        return None
    n = len(valores)
    rank = sum(1 for v in valores if v < score)
    return round(rank / n * 100)


def calcular_stats(domain: Optional[str] = None) -> Dict[str, Any]:
    """Retorna estadísticas agregadas del benchmark global.

    Solo considera entradas de la JUEZ_VERSION actual. Entradas de versiones
    anteriores se ignoran porque el cálculo de dimensiones y pesos cambió.

    Si domain es especificado, filtra por ese dominio normalizado.
    Siempre retorna también las stats globales para comparación.
    """
    data = _cargar()
    raw_entries = data.get("entries", [])
    # Filtrar solo entradas de la versión actual del esquema de scoring
    all_entries = [e for e in raw_entries if e.get("juez_version", 1) == JUEZ_VERSION]
    if not all_entries:
        return {"n": 0, "n_domain": 0}

    domain_norm = normalizar_dominio(domain) if domain else None
    entries_domain = [e for e in all_entries if domain_norm and e.get("domain") == domain_norm]
    entries = all_entries  # stats globales siempre sobre todos

    scores_gen = [e["score_general"] for e in entries]

    dim_stats: Dict[str, Optional[float]] = {}
    for dim in _DIMENSION_LABELS:
        vals = [e.get("dimensiones", {}).get(dim, 0.0) for e in entries if e.get("dimensiones", {}).get(dim, 0.0) > 0]
        dim_stats[dim] = _mean(vals)

    all_cats: set = set()
    for e in entries:
        all_cats.update(e.get("por_categoria", {}).keys())

    cat_stats: Dict[str, Optional[float]] = {}
    for cat in all_cats:
        vals = [e["por_categoria"][cat] for e in entries if cat in e.get("por_categoria", {})]
        cat_stats[cat] = _mean(vals)

    # Stats de dominio específico
    domain_stats: Dict[str, Any] = {}
    if entries_domain:
        scores_dom = [e["score_general"] for e in entries_domain]
        domain_stats = {
            "n": len(entries_domain),
            "score_general_mean": _mean(scores_dom),
            "scores_raw": scores_dom,
            "dimensiones": {
                dim: _mean([e.get("dimensiones", {}).get(dim, 0.0) for e in entries_domain if e.get("dimensiones", {}).get(dim, 0.0) > 0])
                for dim in _DIMENSION_LABELS
            },
            "por_categoria": {
                cat: _mean([e["por_categoria"][cat] for e in entries_domain if cat in e.get("por_categoria", {})])
                for cat in all_cats
            },
        }

    return {
        "n": len(entries),
        "domain": domain_norm,
        "domain_stats": domain_stats,
        "score_general_mean": _mean(scores_gen),
        "score_general_min": round(min(scores_gen), 1),
        "score_general_max": round(max(scores_gen), 1),
        "dimensiones": dim_stats,
        "por_categoria": cat_stats,
        "scores_raw": scores_gen,
    }


def generar_seccion_benchmark(scores_actuales: Dict[str, Any], domain: str = "") -> str:
    """Genera la sección de texto del reporte con posición en benchmark.

    scores_actuales: el dict de scores del agente evaluado (misma estructura
    que build_snapshot / calcular_scorecard).
    domain: dominio del agente evaluado (para comparación por dominio).
    """
    stats = calcular_stats(domain=domain)
    n = stats.get("n", 0)

    lineas: List[str] = []

    def L(txt: str = "") -> None:
        lineas.append(txt)

    L("")
    L("--- 0.5 POSICION EN BENCHMARK GLOBAL " + "-" * 42)
    L("")

    if n < 2:
        L("  Sin suficientes datos de benchmark aun.")
        L(f"  (Se necesitan al menos 2 evaluaciones — hay {n} registrada{'s' if n != 1 else ''}.)")
        L("")
        return "\n".join(lineas)

    score_actual = scores_actuales.get("score_general", 0.0)
    domain_norm = stats.get("domain")
    domain_stats = stats.get("domain_stats", {})
    n_domain = domain_stats.get("n", 0)

    # Encabezado — usar dominio si hay suficientes datos, si no global
    usar_dominio = domain_norm and n_domain >= 2
    referencia = domain_stats if usar_dominio else stats
    n_ref = n_domain if usar_dominio else n
    prom_ref = referencia.get("score_general_mean", 0.0) or 0.0
    pct = _percentil(score_actual, referencia.get("scores_raw", []))
    diff_ref = score_actual - prom_ref
    arrow = "▲" if diff_ref >= 0 else "▼"
    mejor_que = round((pct or 0) * n_ref / 100)

    if usar_dominio:
        L(f"  Dominio detectado              : {domain_norm}")
        L(f"  Agentes en tu dominio          : {n_domain}  (total industria: {n})")
    else:
        L(f"  Evaluaciones en base de datos  : {n} agentes")
        if domain_norm and n_domain < 2:
            L(f"  Dominio detectado              : {domain_norm}  (sin suficientes pares en tu dominio aun)")

    L(f"  Score general tu agente        : {score_actual:.1f}%")
    label_ref = f"promedio {domain_norm}" if usar_dominio else "promedio industria"
    L(f"  {label_ref.capitalize():<31}: {prom_ref:.1f}%  ({arrow} {diff_ref:+.1f}pp)")
    if pct is not None:
        base_label = f"{n_domain} agentes de tu dominio" if usar_dominio else f"{n} agentes"
        L(f"  Tu posicion                    : percentil {pct}  (mejor que {mejor_que}/{n_ref} — {base_label})")

    # Referencia global adicional si estamos usando dominio
    if usar_dominio:
        prom_global = stats.get("score_general_mean", 0.0) or 0.0
        diff_global = score_actual - prom_global
        arrow_g = "▲" if diff_global >= 0 else "▼"
        L(f"  vs promedio industria total    : {prom_global:.1f}%  ({arrow_g} {diff_global:+.1f}pp)")
    L("")

    # Dimensiones vs promedio
    dim_bench = referencia.get("dimensiones", {})
    hay_dims = any(dim_bench.get(k) is not None for k in _DIMENSION_LABELS if (scores_actuales.get(k) or 0) > 0)
    if hay_dims:
        sufijo = f" ({domain_norm})" if usar_dominio else ""
        L(f"  Dimensiones vs promedio{sufijo}:")
        for key, label in _DIMENSION_LABELS.items():
            val_act = scores_actuales.get(key) or 0.0
            val_bench = dim_bench.get(key)
            if val_act == 0.0 or val_bench is None:
                continue
            diff = val_act - val_bench
            arrow_d = "▲" if diff >= 0.5 else ("▼" if diff <= -0.5 else " ")
            L(f"    {arrow_d} {label:<28} {val_act:5.1f}%  vs  {val_bench:5.1f}%  ({diff:+.1f}pp)")
        L("")

    # Categorías vs promedio
    cat_actual = scores_actuales.get("por_categoria") or {}
    cat_bench = referencia.get("por_categoria", {})
    hay_cats = any(cat_bench.get(c) is not None for c in cat_actual)
    if hay_cats:
        sufijo = f" ({domain_norm})" if usar_dominio else ""
        L(f"  Categorias de prueba vs promedio{sufijo}:")
        for cat in sorted(cat_actual.keys()):
            val_act = cat_actual.get(cat) or 0.0
            val_bench = cat_bench.get(cat)
            if val_bench is None:
                continue
            label = _CATEGORY_LABELS.get(cat, cat)
            diff = val_act - val_bench
            arrow_c = "▲" if diff >= 5 else ("▼" if diff <= -5 else " ")
            L(f"    {arrow_c} {label:<28} {val_act:5.1f}%  vs  {val_bench:5.1f}%  ({diff:+.1f}pp)")
        L("")

    L(f"  (Benchmark acumulado — Juez v{JUEZ_VERSION}.0 · Lambda Analytics)")
    L("")
    return "\n".join(lineas)
