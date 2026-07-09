"""Regresion de un bug real: `--project algo.json` (modo legacy, no carpeta)
evaluaba SIEMPRE 0 componentes en silencio y reportaba 100/100 cuando el
archivo era un export CRUDO de n8n (tiene 'nodes'/'connections' pero no la
clave 'componentes' que el parser legacy esperaba). Un usuario podia pensar
que su flujo estaba perfecto sin haberse revisado nada.

`parse_legacy_project_file` ahora reconoce ambos formatos y, cuando ninguno
aplica, el CLI falla con un mensaje claro en vez de imprimir un score falso.
"""
from __future__ import annotations

import json

from juez.colmena.colmena import Componente, parse_legacy_project_file

_FLUJO_N8N_CRUDO = {
    "name": "notifica-cliente",
    "nodes": [
        {"id": "1", "name": "Webhook", "type": "n8n-nodes-base.webhook", "parameters": {}},
        {"id": "2", "name": "Enviar Email", "type": "n8n-nodes-base.emailSend", "parameters": {}},
    ],
    "connections": {
        "Webhook": {"main": [[{"node": "Enviar Email", "type": "main", "index": 0}]]},
    },
}


def test_export_crudo_de_n8n_se_autoenvuelve_en_un_componente():
    """Antes: data.get('componentes', []) -> [] siempre, evaluacion vacia y 100/100 falso."""
    componentes = parse_legacy_project_file(_FLUJO_N8N_CRUDO, fallback_name="fallback")
    assert len(componentes) == 1
    assert componentes[0].kind == "n8n"
    assert componentes[0].nombre == "notifica-cliente"
    assert componentes[0].workflow_json == _FLUJO_N8N_CRUDO


def test_export_crudo_sin_name_usa_fallback():
    flujo = {k: v for k, v in _FLUJO_N8N_CRUDO.items() if k != "name"}
    componentes = parse_legacy_project_file(flujo, fallback_name="mi_flujo")
    assert componentes[0].nombre == "mi_flujo"


def test_formato_legacy_wrapped_sigue_funcionando():
    data = {
        "project_id": "demo",
        "componentes": [{"kind": "prompt", "nombre": "agente", "prompt": "hola"}],
    }
    componentes = parse_legacy_project_file(data, fallback_name="fallback")
    assert len(componentes) == 1
    assert isinstance(componentes[0], Componente)
    assert componentes[0].prompt == "hola"


def test_componentes_explicito_vacio_se_respeta_tal_cual():
    """{'componentes': []} es una declaracion explicita del usuario (0 a proposito),
    distinto de un archivo que ni siquiera tiene la clave -- no debe auto-envolverse
    ni tratarse como error."""
    data = {"project_id": "legacy", "componentes": []}
    componentes = parse_legacy_project_file(data, fallback_name="fallback")
    assert componentes == []


def test_json_irreconocible_devuelve_vacio_para_que_el_caller_falle_fuerte():
    data = {"algo": "que no es ni componentes ni un flujo n8n"}
    componentes = parse_legacy_project_file(data, fallback_name="fallback")
    assert componentes == []


def test_cli_falla_fuerte_en_vez_de_100_100_falso():
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "irreconocible.json"
        config.write_text(json.dumps({"algo": "raro"}), encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [sys.executable, "-m", "juez", "colmena", "--project", str(config)],
            cwd=base, env=env, text=True, capture_output=True, check=False, timeout=30,
        )
        assert proc.returncode != 0
        assert "No se detectaron componentes evaluables" in proc.stderr


def test_cli_evalua_de_verdad_un_export_crudo_de_n8n():
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "flujo.json"
        config.write_text(json.dumps(_FLUJO_N8N_CRUDO), encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [sys.executable, "-m", "juez", "colmena", "--project", str(config)],
            cwd=base, env=env, text=True, capture_output=True, check=False, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        assert "Componentes        : 1" in proc.stdout
        assert "Componentes        : 0" not in proc.stdout
