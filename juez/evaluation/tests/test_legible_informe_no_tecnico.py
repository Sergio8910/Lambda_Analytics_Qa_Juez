"""Tests del informe unico no tecnico (3 secciones: seguridad, funcional,
tecnico) para un lector sin background tecnico.
"""
from __future__ import annotations

from juez.evaluation.reporting.legible import render_informe_no_tecnico

_PROBLEMAS = [
    {"tipo": "Seguridad", "descripcion": "API key hardcodeada en 'Enviar Notificacion'",
     "nodo": "Enviar Notificacion", "severidad": "ALTO"},
    {"tipo": "Configuracion", "descripcion": "usa credencial de prueba: 'staging-db'",
     "nodo": "Guardar Cliente", "severidad": "ALTO"},
    {"tipo": "Objetivo no cumplido", "descripcion": "el flujo no envia el correo de confirmacion",
     "nodo": "Enviar Correo", "severidad": "CRITICO"},
    {"tipo": "Resiliencia", "descripcion": "agente sin retryOnFail",
     "nodo": "Agente Principal", "severidad": "MEDIO"},
]


def test_informe_incluye_las_3_secciones():
    informe = render_informe_no_tecnico("Flujo Test", "cumple_parcial", 62.0, _PROBLEMAS)
    assert "SEGURIDAD" in informe
    assert "FUNCIONAMIENTO" in informe
    assert "CONSTRUCCION TECNICA" in informe


def test_informe_no_pierde_ningun_hallazgo():
    informe = render_informe_no_tecnico("Flujo Test", "cumple_parcial", 62.0, _PROBLEMAS)
    for p in _PROBLEMAS:
        assert p["descripcion"] in informe


def test_informe_traduce_severidad_a_lenguaje_simple():
    informe = render_informe_no_tecnico("Flujo Test", "cumple_parcial", 62.0, _PROBLEMAS)
    assert "[Urgente]" in informe  # CRITICO
    assert "[Importante]" in informe  # ALTO
    assert "[Moderado]" in informe  # MEDIO
    assert "[CRITICO]" not in informe
    assert "[ALTO]" not in informe
    assert "[MEDIO]" not in informe


def test_informe_sin_hallazgos_en_una_seccion_lo_dice_explicitamente():
    solo_seguridad = [_PROBLEMAS[0]]
    informe = render_informe_no_tecnico("Flujo Test", "cumple", 90.0, solo_seguridad)
    assert "Sin hallazgos en esta seccion." in informe


def test_seccion_correcta_por_conteo():
    informe = render_informe_no_tecnico("Flujo Test", "cumple_parcial", 62.0, _PROBLEMAS)
    # 2 en seguridad (Seguridad + Configuracion/credencial), 1 funcional, 1 tecnico
    assert "SEGURIDAD (2)" in informe
    assert "FUNCIONAMIENTO (1)" in informe
    assert "CONSTRUCCION TECNICA (1)" in informe


def test_informe_incluye_resumen_ejecutivo_arriba():
    informe = render_informe_no_tecnico("Flujo Test", "cumple", 95.0, [])
    assert "RESUMEN EJECUTIVO" in informe
    assert informe.index("RESUMEN EJECUTIVO") < informe.index("SEGURIDAD")
