"""Genera datos canónicos sintéticos determinísticos para una corrida e2e.

Mismo `(batch_id, plan_idx)` siempre produce el mismo snapshot — facilita
debugging y reproducibilidad. Sin LLM, sin red.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("juez.synthetic.snapshot_factory")

# Catálogo realista de ambientes típicos de un inventario inmobiliario
_AMBIENTES_DISPONIBLES = [
    "Cocina", "Sala", "Comedor", "Baño social", "Baño principal",
    "Hall de entrada", "Habitación principal", "Habitación 1", "Habitación 2",
    "Estudio", "Patio", "Balcón", "Zona de ropas", "Garaje",
]


def make_synthetic_data(batch_id: str, plan_idx: int = 1) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Genera `(expected_snapshot_dict, canonical_data)` determinísticos.

    Returns:
        - `expected_snapshot_dict`: shape compatible con
          `verificador.schemas.ExpectedSnapshot` (Pydantic la valida).
        - `canonical_data`: datos auxiliares que el `MockToolRunner` usa
          para responder coherentemente al MockAgent (contrato_id,
          inventario_id, propietario, etc).
    """
    rng = random.Random(f"{batch_id}-{plan_idx}")

    # 3 a 5 ambientes — suficiente diversidad pero no excesivo
    n_amb = rng.randint(3, 5)
    ambientes = rng.sample(_AMBIENTES_DISPONIBLES, k=n_amb)

    # Fotos: 4-12 por ambiente. Total típico: 20-50.
    fotos_por_ambiente = {amb: rng.randint(4, 12) for amb in ambientes}
    total_fotos = sum(fotos_por_ambiente.values())

    contrato_id = f"JUEZ-E2E-{batch_id[:8].upper()}-{plan_idx:02d}"
    inventario_id = 99000 + (rng.randint(1, 999))
    propietario = "Propietario Sintético"
    arrendatario = "Arrendatario Sintético"
    tipo_inventario = "INICIAL"

    expected_snapshot = {
        "artifact_id": contrato_id,
        "counts": {
            "fotos": total_fotos,
            "ambientes": n_amb,
        },
        "structure": {
            "ambientes": ambientes,
            "fotos_por_ambiente": fotos_por_ambiente,
            "tipo_inventario": tipo_inventario,
        },
        "required_strings": [contrato_id, propietario, tipo_inventario],
    }

    canonical_data = {
        "source": "synthetic",
        "contrato_id": contrato_id,
        "inventario_id": inventario_id,
        "propietario": propietario,
        "arrendatario": arrendatario,
        "tipo_inventario": tipo_inventario,
        "ambientes": ambientes,
        "fotos_por_ambiente": fotos_por_ambiente,
        "total_fotos": total_fotos,
    }

    return expected_snapshot, canonical_data


def make_data(
    batch_id: str,
    plan_idx: int = 1,
    real_inventario_id: Optional[int] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Dispatcher: si `real_inventario_id` está set, lee BD productiva read-only.
    Si no, usa el factory sintético determinístico.

    Si la lectura de BD falla (BD caída, inventario no existe, etc.) se
    **cae a sintético** y se loggea el motivo — el caso e2e sigue corriendo
    para no romper el batch.
    """
    if real_inventario_id is None:
        return make_synthetic_data(batch_id, plan_idx)

    try:
        from .real_db_source import make_real_db_data, RealDbError
        return make_real_db_data(real_inventario_id)
    except Exception as exc:
        log.warning(
            "real_db_source falló (cae a sintético): %s: %s",
            type(exc).__name__, exc,
        )
        return make_synthetic_data(batch_id, plan_idx)
