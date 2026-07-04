from __future__ import annotations

import random
from typing import Any, Dict, List


def synthesize_context(seed: int, n_nodes: int = 6) -> List[Dict[str, Any]]:
    rnd = random.Random(seed)
    productos = [
        "detergente líquido 1L",
        "jabón en polvo 1kg",
        "leche entera 1L",
        "arroz 1kg",
        "café 250g",
        "pan integral 500g",
        "agua 1.5L",
        "yogurt natural 1L",
    ]
    nodos: List[Dict[str, Any]] = []
    for i in range(n_nodes):
        item = rnd.choice(productos)
        precio = round(rnd.uniform(1.2, 9.5), 2)
        nodos.append(
            {
                "id": f"ctx-{i+1:02d}",
                "text": f"{item}: ${precio}",
                "source": "synthetic",
            }
        )
    # Distractor controlado
    nodos.append(
        {
            "id": f"ctx-{n_nodes+1:02d}",
            "text": "Nota: precios sujetos a disponibilidad.",
            "source": "synthetic",
        }
    )
    return nodos
