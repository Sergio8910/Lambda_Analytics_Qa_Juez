"""CostMeter — tracking de consumo de tokens en el modo e2e sintético.

Se instancia UN CostMeter por batch (pool.py). Cada MockAgent recibe el meter
por constructor y reporta cada respuesta del LLM (prompt + completion tokens).
Al final del batch, el pool lee `summary()` y lo incrusta en el BatchResult
para que el reporter pueda imprimirlo.

Pricing (USD por 1M tokens) en `_PRICING` — precios públicos OpenAI a la fecha
de implementación. Si el modelo no está registrado, se asume costo 0 (no
explota el reporte, pero loguea warning una vez).

Thread-safe: el pool ejecuta varias conversaciones en paralelo con
ThreadPoolExecutor, todas tracking sobre el mismo meter.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict

log = logging.getLogger("juez.synthetic.cost_meter")

# Precios públicos OpenAI en USD por 1M tokens (input / output).
# Fuente: openai.com/api/pricing (snapshot al implementar el módulo).
# Si querés agregar otro modelo, sumalo acá.
_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini":        {"in": 0.15, "out": 0.60},
    "gpt-4o":             {"in": 2.50, "out": 10.00},
    "gpt-4-turbo":        {"in": 10.00, "out": 30.00},
    "gpt-4":              {"in": 30.00, "out": 60.00},
    "gpt-3.5-turbo":      {"in": 0.50, "out": 1.50},
    "o1-mini":            {"in": 3.00, "out": 12.00},
    "o1-preview":         {"in": 15.00, "out": 60.00},
}

# Modelos ya warnings'eados — evitamos spam de logs si aparecen muchas veces.
_WARNED_UNKNOWN: set = set()


def _price_for(model: str) -> Dict[str, float]:
    """Retorna {'in': float, 'out': float} en USD / 1M tokens.
    Si el modelo no está en la tabla, retorna 0/0 y loguea warning una sola vez."""
    if not model:
        return {"in": 0.0, "out": 0.0}
    if model in _PRICING:
        return _PRICING[model]
    # Match prefix-insensitive: "gpt-4o-mini-2024-07-18" → "gpt-4o-mini"
    for known, prices in _PRICING.items():
        if model.startswith(known):
            return prices
    if model not in _WARNED_UNKNOWN:
        _WARNED_UNKNOWN.add(model)
        log.warning("CostMeter: modelo desconocido '%s' — costo asumido $0.", model)
    return {"in": 0.0, "out": 0.0}


class CostMeter:
    """Acumulador thread-safe de tokens consumidos por modelo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # {model: {"prompt_tokens": int, "completion_tokens": int, "calls": int}}
        self._tally: Dict[str, Dict[str, int]] = {}

    @property
    def tally(self) -> Dict[str, Dict[str, int]]:
        """Snapshot del estado (copia — mutar no afecta al meter)."""
        with self._lock:
            return {m: dict(v) for m, v in self._tally.items()}

    def track(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Incrementa el contador para `model`. Una llamada == una respuesta del LLM.

        Si prompt_tokens o completion_tokens vienen None o no-numéricos, los
        coercemos a 0 — no rompemos la corrida por un usage faltante.
        """
        try:
            p = int(prompt_tokens or 0)
        except (TypeError, ValueError):
            p = 0
        try:
            c = int(completion_tokens or 0)
        except (TypeError, ValueError):
            c = 0
        key = model or "unknown"
        with self._lock:
            entry = self._tally.setdefault(
                key,
                {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
            )
            entry["prompt_tokens"] += p
            entry["completion_tokens"] += c
            entry["calls"] += 1

    def summary(self) -> Dict[str, Any]:
        """Construye el resumen final del batch:

            {
              "total_calls": int,
              "total_prompt_tokens": int,
              "total_completion_tokens": int,
              "total_tokens": int,
              "total_cost_usd": float,        # redondeado a 4 decimales
              "by_model": {
                model: {
                  "prompt_tokens": int,
                  "completion_tokens": int,
                  "calls": int,
                  "cost_usd": float,
                },
                ...
              }
            }
        """
        with self._lock:
            snapshot = {m: dict(v) for m, v in self._tally.items()}

        total_calls = 0
        total_prompt = 0
        total_completion = 0
        total_cost = 0.0
        by_model: Dict[str, Dict[str, Any]] = {}

        for model, entry in snapshot.items():
            p = entry["prompt_tokens"]
            c = entry["completion_tokens"]
            calls = entry["calls"]
            price = _price_for(model)
            cost = (p / 1_000_000.0) * price["in"] + (c / 1_000_000.0) * price["out"]
            by_model[model] = {
                "prompt_tokens": p,
                "completion_tokens": c,
                "calls": calls,
                "cost_usd": round(cost, 6),
            }
            total_calls += calls
            total_prompt += p
            total_completion += c
            total_cost += cost

        return {
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "total_cost_usd": round(total_cost, 4),
            "by_model": by_model,
        }
