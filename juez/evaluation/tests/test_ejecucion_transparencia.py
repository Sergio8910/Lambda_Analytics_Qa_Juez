"""Transparencia de ejecución: el contrato declara, por CAPA, si el veredicto
viene de ejecución REAL contra el agente o de una SIMULACION. Confundir ambos
es lo que destruye la credibilidad de un juez, asi que se hace explicito.
"""
from __future__ import annotations

from juez.api.runner import _ejecucion_transparencia


def _capas(**kw):
    base = dict(
        modo_ejecucion="sandbox", incluir_conversaciones=True,
        incluir_dinamicas=False, hay_agentes=True, conversacion={"nodos": []},
    )
    base.update(kw)
    return _ejecucion_transparencia(**base)["capas"]


def test_analisis_estatico_siempre_es_real():
    assert _capas()["analisis_estatico"]["tipo"] == "real"


def test_conversaciones_reales_se_marcan_real():
    capas = _capas(modo_ejecucion="real", conversacion={"nodos": [{}]})
    assert capas["conversaciones"]["tipo"] == "real"
    assert "webhook" in capas["conversaciones"]["detalle"].lower()


def test_conversaciones_sandbox_se_marcan_simulado():
    capas = _capas(modo_ejecucion="sandbox", conversacion={"nodos": [{}]})
    assert capas["conversaciones"]["tipo"] == "simulado"
    assert "simul" in capas["conversaciones"]["detalle"].lower()


def test_conversacion_fallida_no_se_marca_ni_real_ni_simulada():
    capas = _capas(modo_ejecucion="real", conversacion={"error": "webhook caido"})
    assert capas["conversaciones"]["tipo"] == "fallida"


def test_sin_conversaciones_se_marca_no_ejecutadas():
    capas = _capas(incluir_conversaciones=False)
    assert capas["conversaciones"]["tipo"] == "no_ejecutadas"


def test_obreras_dinamicas_siempre_simulado_cuando_activas():
    capas = _capas(incluir_dinamicas=True)
    assert capas["obreras_dinamicas"]["tipo"] == "simulado"
    assert "no ejecuta el agente real" in capas["obreras_dinamicas"]["detalle"].lower()


def test_obreras_dinamicas_desactivadas():
    capas = _capas(incluir_dinamicas=False)
    assert capas["obreras_dinamicas"]["tipo"] == "no_ejecutadas"


def test_modo_refleja_sin_conversaciones():
    t = _ejecucion_transparencia(
        modo_ejecucion="real", incluir_conversaciones=False,
        incluir_dinamicas=False, hay_agentes=True, conversacion=None,
    )
    assert t["modo"] == "sin_conversaciones"
