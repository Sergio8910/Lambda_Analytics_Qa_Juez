from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional

from .schemas import PromptProfile
from ..report_models import TestCase


_DOMAIN_ITEMS: dict[str, list[str]] = {
    "retail": [
        "detergente líquido 1L",
        "jabón en polvo 1kg",
        "leche entera 1L",
        "arroz 1kg",
        "café 250g",
        "pan integral 500g",
        "agua 1.5L",
        "yogurt natural 1L",
    ],
    "support": [
        "impresora no reconoce cartucho",
        "router sin conexión",
        "computadora con pantalla azul",
        "wifi inestable",
        "actualización fallida",
        "audio sin salida",
    ],
    "autos": [
        "cambio de aceite 5W-30",
        "batería 12V",
        "filtro de aire",
        "pastillas de freno",
        "neumáticos 205/55R16",
    ],
    "finance": [
        "comisión de mantenimiento",
        "límite de tarjeta",
        "saldo disponible",
        "fecha de corte",
        "tasa de interés",
    ],
    "travel": [
        "reserva de hotel 3 noches",
        "vuelo ida y vuelta",
        "equipaje 23kg",
        "política de cambios",
        "horario de check-in",
    ],
    "general": [
        "producto principal",
        "detalle requerido",
        "dato relevante",
        "condición clave",
        "observación",
    ],
}


_SUPPORT_SOLUTIONS = [
    "Solución recomendada: reiniciar el equipo y actualizar los controladores.",
    "Acción sugerida: verificar conexiones y reiniciar el dispositivo.",
    "Procedimiento: apagar, esperar 30 segundos y encender nuevamente.",
]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúñ0-9]+", (text or "").lower())


def _value_for_item(domain: str, item: str, rnd: random.Random) -> str:
    if domain == "retail":
        return f"${round(rnd.uniform(1.2, 12.5), 2)}"
    if domain == "support":
        return rnd.choice(_SUPPORT_SOLUTIONS)
    if domain == "autos":
        return f"Precio estimado: ${round(rnd.uniform(25, 220), 2)}"
    if domain == "finance":
        return f"Valor: ${round(rnd.uniform(5, 120), 2)}"
    if domain == "travel":
        return f"Detalle: {rnd.choice(['confirmado', 'pendiente', 'sujeto a disponibilidad'])}"
    return f"Valor: {round(rnd.uniform(1, 50), 2)}"


def generate_context_for_case(
    profile: PromptProfile,
    tc: TestCase,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    base_seed = seed or 0
    derived = base_seed + sum(ord(c) for c in tc.case_id)
    rnd = random.Random(derived)

    domain = profile.domain or "general"
    base_items = list(_DOMAIN_ITEMS.get(domain, _DOMAIN_ITEMS["general"]))
    if profile.keywords:
        for kw in profile.keywords:
            if kw not in base_items and len(kw) > 3:
                base_items.append(kw)

    n_chunks = rnd.randint(3, 8)
    chunks: List[Dict[str, Any]] = []
    input_tokens = _tokenize(tc.input or "")
    primary = None
    for item in base_items:
        if any(tok in item.lower() for tok in input_tokens):
            primary = item
            break
    if primary:
        chunks.append(
            {
                "id": "CTX-1",
                "text": f"{primary}: {_value_for_item(domain, primary, rnd)}",
                "source": "synthetic",
                "meta": {"case_id": tc.case_id, "primary": True},
            }
        )
    remaining = n_chunks - (1 if primary else 0)
    for i in range(remaining):
        item = rnd.choice(base_items)
        chunks.append(
            {
                "id": f"CTX-{i+2}" if primary else f"CTX-{i+1}",
                "text": f"{item}: {_value_for_item(domain, item, rnd)}",
                "source": "synthetic",
                "meta": {"case_id": tc.case_id},
            }
        )
    if any(tag in tc.tags for tag in ["edge", "adversarial", "stress"]):
        distractor = {
            "id": "CTX-D",
            "text": "Nota: información adicional no relacionada.",
            "source": "synthetic",
            "meta": {"distractor": True},
        }
        if len(chunks) >= n_chunks:
            chunks[-1] = distractor
        else:
            chunks.append(distractor)
    if "adversarial" in tc.tags and len(chunks) > 4:
        chunks = chunks[:4]
    return chunks
