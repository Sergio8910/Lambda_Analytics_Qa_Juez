"""Tests de La Colmena (proyecto con n8n inline + prompt, sin red)."""
from __future__ import annotations

import juez.colmena.obreras_dinamicas as od
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
        Componente(kind="prompt", nombre="Lia", prompt="Responde."),
    ]


def test_colmena_estatica_consolida():
    r = run_colmena("colviva-test", _proyecto(), incluir_dinamicas=False)
    assert r.componentes == 2
    # SSRF a metadata cloud = crítico
    assert r.resumen_severidad.get("critico", 0) >= 1
    assert r.veredicto == "NECESITA TRABAJO"
    assert any(h["obrera"].startswith("Guardiana") for h in r.hallazgos)


def test_colmena_reporte_txt():
    r = run_colmena("colviva-test", _proyecto(), incluir_dinamicas=False)
    txt = render_colmena_report(r)
    assert "LA COLMENA" in txt and "colviva-test" in txt and "NECESITA TRABAJO" in txt


def test_dinamicas_degradan_sin_llm(monkeypatch=None):
    # Fuerza el camino "sin OPENAI_API_KEY" de forma determinista (sin red/costo).
    original = od._llm_disponible
    od._llm_disponible = lambda: False
    try:
        r = run_colmena("colviva-test", _proyecto(), incluir_dinamicas=True)
        # las 7 corren; adversarial/edge dejan aviso INFO en vez de fallar
        obreras = {h["obrera"] for h in r.hallazgos}
        assert any(o.startswith("Exploradora") for o in obreras)
        assert any(o.startswith("Niñera") for o in obreras)
        assert any(o.startswith("Performance") for o in obreras)
        # sigue detectando el crítico estático
        assert r.resumen_severidad.get("critico", 0) >= 1
    finally:
        od._llm_disponible = original


def test_colmena_limpio_sin_criticos():
    wf_limpio = {
        "name": "ok", "nodes": [
            {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
            {"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
             "parameters": {"method": "GET", "url": "https://api.publica.com/x"}},
        ],
        "connections": {"Trigger": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]}},
    }
    r = run_colmena("limpio", [Componente(kind="n8n", nombre="ok", workflow_json=wf_limpio)],
                    incluir_dinamicas=False)
    assert r.resumen_severidad.get("critico", 0) == 0
