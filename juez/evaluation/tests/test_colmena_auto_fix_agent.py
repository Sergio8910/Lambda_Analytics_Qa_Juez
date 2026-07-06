from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from juez.colmena.auto_fix_agent import render_auto_fix_agent_report, run_auto_fix_agent
from juez.colmena.colmena import Componente


def _workflow_ssrf():
    return {
        "name": "consulta-precios",
        "nodes": [
            {"name": "Webhook", "type": "n8n-nodes-base.webhook", "parameters": {"path": "p"}},
            {
                "name": "Fetch",
                "type": "n8n-nodes-base.httpRequest",
                "parameters": {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"},
            },
        ],
        "connections": {"Webhook": {"main": [[{"node": "Fetch", "type": "main", "index": 0}]]}},
    }


def test_auto_fix_agent_resuelve_ssrf_en_memoria():
    componentes = [Componente(kind="n8n", nombre="consulta-precios", workflow_json=_workflow_ssrf())]

    with TemporaryDirectory() as tmp:
        result = run_auto_fix_agent(
            "colviva-test",
            componentes,
            max_iteraciones=2,
            incluir_dinamicas=False,
            apply_changes=False,
            git=False,
            output_dir=Path(tmp),
        )

    assert result.score_final > result.score_inicial
    assert result.iteraciones
    assert result.iteraciones[0].validacion is not None
    assert result.iteraciones[0].validacion.accion == "MERGE"
    assert not any("169.254.169.254" in h.get("descripcion", "") for h in result.colmena_final.hallazgos)
    assert result.audit_log_path


def test_auto_fix_agent_persist_project_json():
    with TemporaryDirectory() as tmp:
        project = Path(tmp) / "colviva.json"
        project.write_text(
            json.dumps(
                {
                    "project_id": "colviva-test",
                    "componentes": [
                        {"kind": "n8n", "nombre": "consulta-precios", "workflow_json": _workflow_ssrf()}
                    ],
                }
            ),
            encoding="utf-8",
        )
        data = json.loads(project.read_text(encoding="utf-8"))
        componentes = [Componente(**c) for c in data["componentes"]]

        result = run_auto_fix_agent(
            "colviva-test",
            componentes,
            project_file=project,
            max_iteraciones=1,
            incluir_dinamicas=False,
            apply_changes=True,
            git=False,
            output_dir=Path(tmp),
        )
        updated = json.loads(project.read_text(encoding="utf-8"))
        url = updated["componentes"][0]["workflow_json"]["nodes"][1]["parameters"]["url"]

        assert result.score_final > result.score_inicial
        assert url == "https://example.invalid/autofix-blocked-url"
        assert "LA COLMENA + AUTO-FIX AGENT" in render_auto_fix_agent_report(result)
