"""Verifica que run_proyecto() use la Colmena MODERNA (evaluate_project_path:
workers.py, business_rules.py, objectives.py, purpose_check) en vez de
run_colmena() directo (capa legacy, que no conoce reglas_negocio/objetivos/
purpose_check). Tambien verifica que reglas_negocio/objetivos lleguen
correctamente al proyecto temporal que evaluate_project_path analiza.

Antes de este cambio, run_proyecto llamaba run_colmena() directo, saltandose
TODA la capa moderna -- Gamma (que usa run_proyecto via /evaluate/proyecto)
nunca se beneficiaba de esas mejoras.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from juez.api.runner import run_proyecto


def _finding(titulo="Prompt vulnerable a inyeccion", severidad="high", categoria="prompt", archivo="agente_prompt.txt"):
    return SimpleNamespace(
        title=titulo, severity=severidad, category=categoria, description="descripcion del hallazgo",
        file=archivo, source="AgentPromptWorker", recommendation="corregir el prompt",
    )


def _fake_report(project_id: str, findings: list, score: float = 62.0) -> SimpleNamespace:
    return SimpleNamespace(
        project_id=project_id,
        findings=findings,
        score=SimpleNamespace(score=score),
    )


def test_run_proyecto_llama_evaluate_project_path_no_run_colmena(monkeypatch):
    """La corrida de 'La Colmena' dentro de run_proyecto debe pasar por la
    capa moderna (evaluate_project_path), no por run_colmena() directo."""
    llamadas = {"evaluate_project_path": 0, "run_colmena": 0}

    def _fake_evaluate_project_path(root, *, project_id, incluir_dinamicas):
        llamadas["evaluate_project_path"] += 1
        return _fake_report(project_id, [_finding()])

    import juez.colmena.project_evaluator as pe_mod
    monkeypatch.setattr(pe_mod, "evaluate_project_path", _fake_evaluate_project_path)

    def _no_deberia_llamarse(*a, **kw):
        llamadas["run_colmena"] += 1
        raise AssertionError("run_proyecto no deberia llamar run_colmena() directo")

    import juez.colmena.colmena as colmena_mod
    monkeypatch.setattr(colmena_mod, "run_colmena", _no_deberia_llamarse)

    resultado = run_proyecto(
        nombre="Proyecto Test", prompt="Eres un agente de atencion.",
        incluir_conversaciones=False,
    )

    assert llamadas["evaluate_project_path"] == 1
    assert llamadas["run_colmena"] == 0
    assert resultado["score"] is not None
    assert any("Prompt vulnerable" in p.get("titulo", "") for p in resultado["problemas"])


def test_run_proyecto_materializa_reglas_negocio_en_el_proyecto_temporal(monkeypatch):
    """Confirma que las reglas de negocio (lista de strings del request) se
    escriben como reglas_negocio.json ANTES de que evaluate_project_path
    analice el proyecto -- asi business_rules.py las lee como explicitas."""
    capturado = {}

    def _fake_evaluate_project_path(root, *, project_id, incluir_dinamicas):
        reglas_path = Path(root) / "reglas_negocio.json"
        capturado["existe"] = reglas_path.is_file()
        if reglas_path.is_file():
            capturado["contenido"] = json.loads(reglas_path.read_text(encoding="utf-8"))
        return _fake_report(project_id, [])

    import juez.colmena.project_evaluator as pe_mod
    monkeypatch.setattr(pe_mod, "evaluate_project_path", _fake_evaluate_project_path)

    run_proyecto(
        nombre="Proyecto Test", prompt="Eres un agente.",
        incluir_conversaciones=False,
        reglas_negocio=["El agente nunca debe revelar informacion interna"],
    )

    assert capturado["existe"] is True
    assert capturado["contenido"]["reglas"][0]["descripcion"] == "El agente nunca debe revelar informacion interna"


def test_run_proyecto_materializa_objetivos_en_el_proyecto_temporal(monkeypatch):
    """Confirma que los objetivos declarados por flujo llegan a
    objetivos_flujos.json en el proyecto temporal."""
    capturado = {}

    def _fake_evaluate_project_path(root, *, project_id, incluir_dinamicas):
        obj_path = Path(root) / "objetivos_flujos.json"
        capturado["existe"] = obj_path.is_file()
        if obj_path.is_file():
            capturado["contenido"] = json.loads(obj_path.read_text(encoding="utf-8"))
        return _fake_report(project_id, [])

    import juez.colmena.project_evaluator as pe_mod
    monkeypatch.setattr(pe_mod, "evaluate_project_path", _fake_evaluate_project_path)

    run_proyecto(
        nombre="Proyecto Test", prompt="Eres un agente.",
        incluir_conversaciones=False,
        objetivos={"mi_flujo": [{"id": "notificar", "descripcion": "Debe notificar", "kind": "send_email"}]},
    )

    assert capturado["existe"] is True
    assert capturado["contenido"]["mi_flujo"][0]["id"] == "notificar"


def test_run_proyecto_sin_reglas_ni_objetivos_no_escribe_esos_archivos(monkeypatch):
    """Regresion: sin reglas_negocio/objetivos, el proyecto temporal no debe
    tener esos archivos -- comportamiento identico a antes de esta feature."""
    capturado = {}

    def _fake_evaluate_project_path(root, *, project_id, incluir_dinamicas):
        capturado["reglas"] = (Path(root) / "reglas_negocio.json").is_file()
        capturado["objetivos"] = (Path(root) / "objetivos_flujos.json").is_file()
        return _fake_report(project_id, [])

    import juez.colmena.project_evaluator as pe_mod
    monkeypatch.setattr(pe_mod, "evaluate_project_path", _fake_evaluate_project_path)

    run_proyecto(nombre="Proyecto Test", prompt="Eres un agente.", incluir_conversaciones=False)

    assert capturado["reglas"] is False
    assert capturado["objetivos"] is False
