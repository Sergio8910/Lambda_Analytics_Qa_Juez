"""Tests de La Colmena (proyecto con n8n inline + prompt, sin red)."""
from __future__ import annotations

from juez.colmena import render_colmena_report, run_colmena
from juez.colmena.colmena import Componente


def _proyecto():
    wf_inseguro = {
        "name": "consulta-precios",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "parameters": {"path": "p"}},
            {"name": "Fetch", "type": "n8n-nodes-base.httpRequest",
             "parameters": {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"}},
        ],
        "connections": {"Webhook": {"main": [[{"node": "Fetch", "type": "main", "index": 0}]]}},
    }
    return [
        Componente(kind="n8n", nombre="consulta-precios", workflow_json=wf_inseguro,
                   objetivos=[{"id": "llamar_api", "kind": "http_request"}]),
        Componente(kind="prompt", nombre="Lia", prompt="Responde."),  # prompt pobre
    ]


def test_colmena_corre_y_consolida():
    r = run_colmena("colviva-test", _proyecto())
    assert r.project_id == "colviva-test"
    assert r.componentes == 2
    assert 0 <= r.score <= 100
    # el SSRF a metadata (169.254.169.254) es crítico -> debe salir
    assert r.resumen_severidad.get("critico", 0) >= 1
    assert r.veredicto == "NECESITA TRABAJO"
    assert any(h["obrera"].startswith("Guardiana") for h in r.hallazgos)


def test_colmena_reporte_txt():
    r = run_colmena("colviva-test", _proyecto())
    txt = render_colmena_report(r)
    assert "LA COLMENA" in txt
    assert "colviva-test" in txt
    assert "NECESITA TRABAJO" in txt
    # obreras dinámicas marcadas como no corridas
    assert "no corridas" in txt.lower() or "opt-in" in txt.lower()


def test_colmena_proyecto_limpio_sin_criticos():
    wf_limpio = {
        "name": "ok", "nodes": [
            {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
            {"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
             "parameters": {"method": "GET", "url": "https://api.publica.com/x"}},
        ],
        "connections": {"Trigger": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]}},
    }
    r = run_colmena("limpio", [Componente(kind="n8n", nombre="ok", workflow_json=wf_limpio)])
    assert r.resumen_severidad.get("critico", 0) == 0
