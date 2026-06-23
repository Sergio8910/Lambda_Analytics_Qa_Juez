"""Modo de QA: técnico, funcional o ambos.

Clasifica cada hallazgo (por su 'tipo') en técnico o funcional y filtra la lista
según el modo elegido. 'tecnico' = cómo está construido (estructura, código,
seguridad, redundancia); 'funcional' = si cumple su propósito (objetivos,
conversación, salida/artefacto, negocio).
"""
from __future__ import annotations

from typing import Any, Dict, List

# Si el 'tipo' contiene alguno de estos, es funcional; si no, técnico.
_FUNCIONAL = (
    "objetivo", "conversa", "intencion", "intención", "task", "negocio",
    "artefacto", "pdf", "respuesta", "grounding", "alucina", "formato",
    "escenario", "rag",
)

MODOS = ("tecnico", "funcional", "ambos")


def clasificar(tipo: str) -> str:
    t = (tipo or "").lower()
    return "funcional" if any(k in t for k in _FUNCIONAL) else "tecnico"


def filtrar_problemas(problemas: List[Dict[str, Any]], modo: str = "ambos") -> List[Dict[str, Any]]:
    modo = (modo or "ambos").lower()
    if modo not in MODOS or modo == "ambos":
        return list(problemas)
    return [p for p in problemas if clasificar(p.get("tipo", "")) == modo]


if __name__ == "__main__":  # ponytail: self-check sin red
    p = [{"tipo": "Seguridad / SSRF"}, {"tipo": "Objetivo no cumplido"}, {"tipo": "Artefacto / PDF"}]
    assert len(filtrar_problemas(p, "tecnico")) == 1
    assert len(filtrar_problemas(p, "funcional")) == 2
    assert len(filtrar_problemas(p, "ambos")) == 3
    print("ok")
