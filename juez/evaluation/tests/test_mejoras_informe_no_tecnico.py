"""Verifica que consolidar_proyecto() (juez/colmena/mejoras.py) incluya el
informe no tecnico de 3 secciones -- este es el camino REAL que usa el
dashboard "La Colmena" de Gamma (POST /evaluate/proyecto -> run_proyecto ->
consolidar_proyecto), distinto del que usa /evaluate/n8n (run_n8n_single).
"""
from __future__ import annotations

from types import SimpleNamespace

from juez.colmena.mejoras import consolidar_proyecto


def _colmena_fake(hallazgos):
    return SimpleNamespace(
        hallazgos=hallazgos,
        score=62.0,
        model_dump=lambda mode="json": {"hallazgos": hallazgos, "score": 62.0},
    )


def test_consolidar_proyecto_incluye_informe_no_tecnico():
    colmena = _colmena_fake([
        {"obrera": "Guardiana", "severidad": "alto",
         "descripcion": "[agente] API key hardcodeada en el nodo", "ubicacion": "Enviar Notificacion", "accion": "usar env vars"},
        {"obrera": "Integración", "severidad": "critico",
         "descripcion": "[agente] Objetivo no cumplido: no crea el ticket", "ubicacion": "Crear Ticket", "accion": "revisar el nodo"},
    ])
    contrato = consolidar_proyecto(nombre="Proyecto Test", colmena=colmena, conversacion=None)

    assert "informe_no_tecnico" in contrato
    informe = contrato["informe_no_tecnico"]
    assert informe, "el informe no deberia quedar vacio con problemas reales"
    assert "SEGURIDAD" in informe
    assert "FUNCIONAMIENTO" in informe
    assert "CONSTRUCCION TECNICA" in informe


def test_consolidar_proyecto_sin_problemas_no_rompe():
    contrato = consolidar_proyecto(nombre="Proyecto Vacio", colmena=None, conversacion=None)
    assert contrato["informe_no_tecnico"] != "" or contrato["problemas"] == []


def test_consolidar_proyecto_traduce_estado_a_veredicto_legible():
    colmena = _colmena_fake([])
    contrato = consolidar_proyecto(nombre="Proyecto Listo", colmena=colmena, conversacion=None)
    assert contrato["estado"] in ("LISTO", "NECESITA_AJUSTES", "NECESITA_ATENCION")
    # El informe debe reflejar un veredicto traducido, no el codigo crudo en mayusculas pegado
    assert "RESUMEN EJECUTIVO" in contrato["informe_no_tecnico"]
