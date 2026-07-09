"""Tests de la categoria 'recorrido_completo': una sola conversacion que
encadena TODAS las tools del agente, para probar cobertura total en un
flujo natural (a diferencia de 'herramienta', que prueba una tool por
conversacion)."""
from __future__ import annotations

from juez.evaluation.contra_agente.generator import (
    _build_recorrido_completo_variantes,
    _generar_planes_heuristicos,
)

_ANALISIS_3_TOOLS = {
    "tools": [
        {"nombre": "consultar_saldo", "tipo": "webhook", "descripcion": "Consultar el saldo de la cuenta.",
         "campos_requeridos": ["cedula"]},
        {"nombre": "agendar_cita", "tipo": "webhook", "descripcion": "Agendar una cita con un asesor.",
         "campos_requeridos": ["fecha", "hora"]},
        {"nombre": "generar_certificado", "tipo": "webhook", "descripcion": "Generar un certificado de cuenta.",
         "campos_requeridos": ["email"]},
    ]
}

_ANALISIS_1_TOOL = {
    "tools": [
        {"nombre": "consultar_saldo", "tipo": "webhook", "descripcion": "Consultar el saldo.",
         "campos_requeridos": ["cedula"]},
    ]
}

_ANALISIS_SIN_TOOLS = {"tools": []}


def test_encadena_todas_las_tools_en_una_sola_conversacion():
    variantes = _build_recorrido_completo_variantes(_ANALISIS_3_TOOLS)
    assert variantes, "se esperaba al menos una variante"
    turns = variantes[0]
    # 2 turnos por tool (necesidad + datos) + 1 de cierre = 7 para 3 tools.
    assert len(turns) == 3 * 2 + 1
    assert turns[-1][1] == "closing"
    texto_completo = " ".join(t[0] for t in turns).lower()
    for tool in _ANALISIS_3_TOOLS["tools"]:
        # La necesidad de cada tool debe reflejarse en el texto de la conversacion.
        palabra_clave = tool["descripcion"].split()[0].lower()
        assert palabra_clave in texto_completo or tool["nombre"] in texto_completo


def test_cada_tool_tiene_su_turno_de_datos_con_metrica_tool_invocation():
    distribucion = {"recorrido_completo": 1}
    plans = _generar_planes_heuristicos(_ANALISIS_3_TOOLS, "Agente Test", distribucion, "batch_test")
    assert len(plans) == 1
    plan = plans[0]
    assert len(plan.turns) == 7
    assert all("tool_invocation" in t.metrics for t in plan.turns)


def test_dos_variantes_con_orden_distinto_para_diversidad():
    variantes = _build_recorrido_completo_variantes(_ANALISIS_3_TOOLS)
    assert len(variantes) == 2
    primer_opener_v1 = variantes[0][0][0]
    primer_opener_v2 = variantes[1][0][0]
    assert primer_opener_v1 != primer_opener_v2


def test_con_menos_de_dos_tools_degrada_a_herramienta_sin_crashear():
    variantes = _build_recorrido_completo_variantes(_ANALISIS_1_TOOL)
    assert variantes  # no vacio, no crashea
    variantes_sin_tools = _build_recorrido_completo_variantes(_ANALISIS_SIN_TOOLS)
    assert variantes_sin_tools


def test_distribucion_respeta_recorrido_completo_cuando_se_pide_explicito():
    distribucion = {"recorrido_completo": 2}
    plans = _generar_planes_heuristicos(_ANALISIS_3_TOOLS, "Agente Test", distribucion, "batch_test")
    assert len(plans) == 2
    assert all(p.category == "recorrido_completo" for p in plans)
