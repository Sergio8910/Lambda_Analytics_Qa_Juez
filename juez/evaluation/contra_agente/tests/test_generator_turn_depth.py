"""Guarda contra la regresion de conversaciones colapsadas a 1 turno.

Contexto real: se observaron en produccion conversaciones de 3 mensajes
(1 solo intercambio) incluso en categorias que deberian tener varios turnos
(happy_path, herramienta, multi_turno). La causa raiz fue que el ejemplo
JSON de pocos-disparos (`_build_generator_prompt`) mostraba un plan de 1 solo
turno pese a que el texto decia "2-3 turnos" -- el LLM ancla su salida a la
estructura del ejemplo por encima de la instruccion en prosa. Estos tests
fijan el comportamiento esperado para que no vuelva a pasar desapercibido.
"""
from __future__ import annotations

from juez.evaluation.contra_agente.generator import (
    _build_generator_prompt,
    _generar_planes_heuristicos,
)

_MIN_TURNS_ESPERADOS = {
    "happy_path": 2,
    "multi_turno": 3,
    "agresivo": 2,
    "contexto_multiple": 2,
}


def test_heuristicos_no_colapsan_a_un_turno():
    """El generador sin LLM debe respetar el minimo de turnos por categoria."""
    distribucion = {cat: 1 for cat in _MIN_TURNS_ESPERADOS}
    plans = _generar_planes_heuristicos({}, "Agente Test", distribucion, "batch_test")
    por_categoria = {p.category: p for p in plans}
    for categoria, minimo in _MIN_TURNS_ESPERADOS.items():
        plan = por_categoria[categoria]
        assert len(plan.turns) >= minimo, (
            f"'{categoria}' genero solo {len(plan.turns)} turno(s), se esperaban >= {minimo}"
        )


def test_limite_caos_seguridad_tienen_al_menos_dos_turnos():
    """limite/caos/seguridad admiten 1-2 turnos por diseno, pero el fallback
    heuristico debe usar 2 (con un segundo turno de insistencia) para
    realmente probar si el agente sostiene el limite, no solo en el primer intento."""
    distribucion = {"limite": 1, "caos": 1, "seguridad": 1}
    plans = _generar_planes_heuristicos({}, "Agente Test", distribucion, "batch_test")
    for plan in plans:
        assert len(plan.turns) >= 2, f"'{plan.category}' solo tiene {len(plan.turns)} turno(s)"


def test_prompt_del_llm_no_ancla_a_un_solo_turno():
    """El ejemplo JSON de few-shot dentro del prompt del generador debe mostrar
    mas de un turno -- de lo contrario el LLM ancla su salida real a un plan
    de 1 solo turno sin importar lo que diga la prosa de las reglas."""
    prompt = _build_generator_prompt({}, "Agente Test", {"happy_path": 1})
    assert '"turn_id": 2' in prompt
    assert '"turn_id": 3' in prompt
