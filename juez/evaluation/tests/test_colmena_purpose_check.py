"""Tests de la obrera 'Proposito' (verificacion semantica de 'cumple su
proposito', reusando la rubrica task_success del motor central del Juez).

Todos deterministicos: la llamada real a OpenAI se monkeypatchea, igual que
el resto de los tests de obreras dinamicas de La Colmena.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from juez.colmena import purpose_check as pc
from juez.colmena.business_rules import load_declared_purposes
from juez.colmena.colmena import Componente


def _componente(nombre: str = "agente_atencion", prompt: str = "Eres un agente de atencion.") -> Componente:
    return Componente(kind="prompt", nombre=nombre, prompt=prompt)


def test_sin_proposito_declarado_no_genera_hallazgo():
    c = _componente()
    assert pc.verificar_proposito(c, purposes={}, cost_meter=None) == []


def test_sin_llm_disponible_degrada_con_info(monkeypatch):
    monkeypatch.setattr(pc, "_llm_disponible", lambda: False)
    c = _componente()
    hallazgos = pc.verificar_proposito(c, purposes={"agente_atencion": "Debe resolver dudas del cliente"}, cost_meter=None)
    assert len(hallazgos) == 1
    assert hallazgos[0]["severidad"] == "info"
    assert "OPENAI_API_KEY" in hallazgos[0]["descripcion"]


def test_score_bajo_genera_hallazgo_alto(monkeypatch):
    monkeypatch.setattr(pc, "_llm_disponible", lambda: True)
    monkeypatch.setattr(
        pc, "_juzgar_proposito",
        lambda prompt, proposito: {
            "entrada_simulada": "¿Pueden ayudarme con mi pedido?",
            "respuesta_simulada": "No puedo ayudarte con eso.",
            "score": 0.1,
            "razon": "El agente no atiende la solicitud real del cliente",
            "_model": "gpt-4o-mini", "_prompt_tokens": 100, "_completion_tokens": 40,
        },
    )
    c = _componente()
    hallazgos = pc.verificar_proposito(c, purposes={"agente_atencion": "EXITO = resuelve la duda. FALLO = la ignora."}, cost_meter=None)
    assert len(hallazgos) == 1
    assert hallazgos[0]["severidad"] == "alto"
    assert "proposito" in hallazgos[0]["descripcion"].lower()


def test_score_alto_no_genera_hallazgo(monkeypatch):
    monkeypatch.setattr(pc, "_llm_disponible", lambda: True)
    monkeypatch.setattr(
        pc, "_juzgar_proposito",
        lambda prompt, proposito: {
            "entrada_simulada": "...", "respuesta_simulada": "...",
            "score": 0.95, "razon": "cumple", "_model": "gpt-4o-mini",
            "_prompt_tokens": 100, "_completion_tokens": 40,
        },
    )
    c = _componente()
    hallazgos = pc.verificar_proposito(c, purposes={"agente_atencion": "algo"}, cost_meter=None)
    assert hallazgos == []


def test_cost_meter_trackea_la_llamada(monkeypatch):
    monkeypatch.setattr(pc, "_llm_disponible", lambda: True)
    monkeypatch.setattr(
        pc, "_juzgar_proposito",
        lambda prompt, proposito: {
            "entrada_simulada": "...", "respuesta_simulada": "...",
            "score": 0.9, "razon": "ok", "_model": "gpt-4o-mini",
            "_prompt_tokens": 123, "_completion_tokens": 45,
        },
    )
    meter = SimpleNamespace(calls=[])
    meter.track = lambda model, p, c: meter.calls.append((model, p, c))
    c = _componente()
    pc.verificar_proposito(c, purposes={"agente_atencion": "algo"}, cost_meter=meter)
    assert meter.calls == [("gpt-4o-mini", 123, 45)]


def test_load_declared_purposes_lee_reglas_negocio_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "reglas_negocio.json").write_text(
            json.dumps({
                "reglas": [],
                "proposito_por_componente": {
                    "agente_atencion": "EXITO = resuelve o escala. FALLO = ignora la consulta.",
                },
            }),
            encoding="utf-8",
        )
        purposes = load_declared_purposes(root)
        assert purposes == {"agente_atencion": "EXITO = resuelve o escala. FALLO = ignora la consulta."}


def test_load_declared_purposes_vacio_sin_archivo():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_declared_purposes(Path(tmp)) == {}
