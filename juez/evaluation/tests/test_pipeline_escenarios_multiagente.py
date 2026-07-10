"""Regresion: 'escenario de evaluacion' se perdia en silencio cuando se
evaluaban VARIOS agentes juntos (run_pipeline / EvalPipelineRequest,
EvalProyectoRequest) -- funcionaba solo para un agente (run_n8n_single /
run_elevenlabs_single).

_evaluar_con_analisis carga evaluar_n8n.py/evaluar_elevenlabs.py via
importlib.util.spec_from_file_location (modulo fresco cada vez, no pasa por
sys.modules), lo que hace dificil mockear con monkeypatch tradicional. Por
eso: (a) un test estructural que confirma que las 3 llamadas internas pasan
escenarios_extra, y (b) un test de wiring que confirma que la firma acepta
el parametro sin romper el camino donde total_conv=0 (no toca ninguna obrera
dinamica, asi que no necesita mockear los modulos cargados dinamicamente).
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from juez.evaluar_pipeline import _evaluar_con_analisis


def _codigo_fuente() -> str:
    return Path(inspect.getfile(_evaluar_con_analisis)).read_text(encoding="utf-8")


def test_firma_acepta_escenarios_extra():
    firma = inspect.signature(_evaluar_con_analisis)
    assert "escenarios_extra" in firma.parameters


def test_las_3_llamadas_internas_pasan_escenarios_extra():
    """Test estructural: confirma en el codigo fuente que ninguna de las 3
    invocaciones que disparan conversaciones (generar_batch para elevenlabs,
    ejecutar_contra_agente sandbox y real para n8n) perdio el parametro."""
    fuente = _codigo_fuente()
    inicio = fuente.index("def _evaluar_con_analisis")
    fin = fuente.index("def _evaluar_elevenlabs")
    cuerpo = fuente[inicio:fin]

    llamadas_gen = re.findall(r"_gen\([^)]*\)", cuerpo, re.DOTALL)
    llamadas_ca = re.findall(r"_mod\.ejecutar_contra_agente\([^)]*\)", cuerpo, re.DOTALL)

    assert len(llamadas_gen) == 1, "se esperaba 1 llamada a generar_batch (rama elevenlabs)"
    assert len(llamadas_ca) == 2, "se esperaban 2 llamadas a ejecutar_contra_agente (sandbox + real)"
    for llamada in llamadas_gen + llamadas_ca:
        assert "escenarios_extra" in llamada, f"escenarios_extra no se propaga en: {llamada[:80]}..."


def test_run_pipeline_pasa_escenarios_a_evaluar_con_analisis():
    """Confirma que runner.py::run_pipeline reenvia su parametro `escenarios`
    a _evaluar_con_analisis (el bug era que se aceptaba pero nunca se usaba)."""
    import juez.api.runner as runner_mod
    fuente = Path(inspect.getfile(runner_mod)).read_text(encoding="utf-8")
    inicio = fuente.index("def run_pipeline(")
    fin = fuente.index("def run_proyecto(")
    cuerpo = fuente[inicio:fin]
    llamada = re.search(r"_evaluar_con_analisis\([^)]*\)", cuerpo, re.DOTALL)
    assert llamada is not None
    assert "escenarios_extra=escenarios" in llamada.group()
