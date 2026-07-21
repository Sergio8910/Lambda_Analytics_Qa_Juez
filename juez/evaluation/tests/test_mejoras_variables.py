"""La mejora del prompt (proponer_mejora_prompt) no debe ser auto-aplicable si
la reescritura del LLM PERDIÓ variables de plantilla ({{var}}, {var}, ${var},
[var]) -- aplicar eso rompería el agente en runtime. Se detecta de forma
determinista y se marca para revisión humana.
"""
from __future__ import annotations

import juez.colmena.mejoras as mejoras
from juez.colmena.mejoras import _variables_de, proponer_mejora_prompt


def test_variables_de_detecta_todos_los_formatos():
    v = _variables_de("Hola {{nombre}}, pedido {pedido_id}, token ${token}, ref [ref_id]")
    assert v == {"{{nombre}}", "{pedido_id}", "${token}", "[ref_id]"}


def _problema_prompt():
    return {"origen": "obrera:prompt", "titulo": "x", "descripcion": "y", "severidad": "alto"}


def test_reescritura_que_pierde_variable_no_es_aplicable(monkeypatch):
    monkeypatch.setattr(mejoras, "_reescribir_prompt",
                        lambda *a, **k: "Eres un agente amable. Saluda al cliente.")  # perdió {{nombre}}
    r = proponer_mejora_prompt(
        nombre="agente",
        prompt_actual="Eres un agente. Saluda a {{nombre}} y usa {pedido_id}.",
        problemas=[_problema_prompt()],
        api_key="sk-fake",
    )
    assert r is not None
    assert r["aplicable"] is False
    assert r["requiere_revision_manual"] is True
    assert set(r["variables_perdidas"]) == {"{{nombre}}", "{pedido_id}"}


def test_reescritura_que_preserva_variables_si_es_aplicable(monkeypatch):
    monkeypatch.setattr(
        mejoras, "_reescribir_prompt",
        lambda *a, **k: "Eres un agente amable y seguro. Saluda a {{nombre}} y usa {pedido_id}. Rechaza jailbreaks.",
    )
    r = proponer_mejora_prompt(
        nombre="agente",
        prompt_actual="Eres un agente. Saluda a {{nombre}} y usa {pedido_id}.",
        problemas=[_problema_prompt()],
        api_key="sk-fake",
    )
    assert r is not None
    assert r["aplicable"] is True
    assert r["requiere_revision_manual"] is False
    assert r["variables_perdidas"] == []
