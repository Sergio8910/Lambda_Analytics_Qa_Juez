"""Test del modo de QA (técnico / funcional / ambos)."""
from __future__ import annotations

from juez.evaluation.qa_mode import clasificar, filtrar_problemas

_PROBLEMAS = [
    {"tipo": "Seguridad / SSRF", "descripcion": "x"},
    {"tipo": "Seguridad / Código", "descripcion": "x"},
    {"tipo": "Objetivo no cumplido", "descripcion": "x"},
    {"tipo": "Artefacto / PDF", "descripcion": "x"},
    {"tipo": "Conversacion", "descripcion": "x"},
]


def test_clasificar():
    assert clasificar("Seguridad / SSRF") == "tecnico"
    assert clasificar("Objetivo no cumplido") == "funcional"
    assert clasificar("Artefacto / PDF") == "funcional"


def test_filtrar_tecnico():
    out = filtrar_problemas(_PROBLEMAS, "tecnico")
    assert len(out) == 2
    assert all(clasificar(p["tipo"]) == "tecnico" for p in out)


def test_filtrar_funcional():
    out = filtrar_problemas(_PROBLEMAS, "funcional")
    assert len(out) == 3


def test_ambos_no_filtra():
    assert len(filtrar_problemas(_PROBLEMAS, "ambos")) == 5
    assert len(filtrar_problemas(_PROBLEMAS, "")) == 5
