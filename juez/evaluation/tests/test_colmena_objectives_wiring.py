"""Verifica que objectives.py (verificacion sintetica de 'cumple su proposito')
quede conectado al escaneo real de proyecto de La Colmena.

Antes de este cambio, `_components_from_inventory` nunca poblaba
`Componente.objetivos`, asi que `_integracion` (que envuelve `verify_objectives`)
siempre hacia short-circuit a `[]` -- la verificacion de objetivos solo era
alcanzable via el endpoint aislado POST /verify/objectives, nunca desde
`evaluate_project_path`. Ahora un manifiesto opcional `objetivos_flujos.json`
en la raiz del proyecto declara objetivos por flujo; sin el archivo, el
comportamiento es identico al de antes (cero regresion).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from juez.colmena.project_evaluator import _components_from_inventory, evaluate_project_path
from juez.colmena.scanner import scan_project

_FLUJO = {
    "name": "registra-cliente",
    "nodes": [
        {"id": "1", "name": "Webhook Registro", "type": "n8n-nodes-base.webhook", "parameters": {"path": "registro"}},
        {"id": "2", "name": "Guardar Cliente", "type": "n8n-nodes-base.postgres", "parameters": {"query": "INSERT INTO clientes (nombre) VALUES ($1)"}},
    ],
    "connections": {
        "Webhook Registro": {"main": [[{"node": "Guardar Cliente", "type": "main", "index": 0}]]},
    },
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _project(base: Path, with_manifest: bool) -> Path:
    root = base / "proyecto_objetivos"
    root.mkdir()
    _write(root / "flujo_registra_cliente.json", json.dumps(_FLUJO))
    if with_manifest:
        manifest = {
            "registra-cliente": [
                {
                    "id": "notificar_cliente",
                    "descripcion": "Debe enviar un correo de confirmacion al cliente registrado",
                    "kind": "send_email",
                }
            ]
        }
        _write(root / "objetivos_flujos.json", json.dumps(manifest))
    return root


def test_sin_manifiesto_objetivos_queda_vacio_como_antes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), with_manifest=False)
        inventory = scan_project(root)
        componentes = _components_from_inventory(root, inventory)
        assert len(componentes) == 1
        assert componentes[0].objetivos == []


def test_manifiesto_pobla_objetivos_del_componente() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), with_manifest=True)
        inventory = scan_project(root)
        componentes = _components_from_inventory(root, inventory)
        assert len(componentes) == 1
        assert len(componentes[0].objetivos) == 1
        assert componentes[0].objetivos[0]["id"] == "notificar_cliente"


def test_objetivo_incumplido_genera_hallazgo_de_integracion_end_to_end() -> None:
    """El flujo NO tiene ningun nodo de envio de correo -> el objetivo declarado
    (send_email) debe quedar incumplido y aparecer como hallazgo real en el
    escaneo completo de proyecto, no solo en el endpoint aislado."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), with_manifest=True)
        report = evaluate_project_path(root)
        assert report.legacy_component_findings, "se esperaba al menos un hallazgo legacy de Integracion"
        textos = " ".join(
            f"{h.get('categoria', '')} {h.get('descripcion', '')}" for h in report.legacy_component_findings
        )
        assert "notificar_cliente" in textos or "Integraci" in textos


def test_sin_manifiesto_no_hay_violacion_pero_si_rastro_info() -> None:
    """Sin manifiesto no debe aparecer una VIOLACION de objetivos (eso seria
    un falso positivo), pero SI un rastro 'info' de que no se verifico -- el
    reporte ya no omite en silencio que el flujo no se contrasto con objetivos."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _project(Path(tmp), with_manifest=False)
        report = evaluate_project_path(root)
        integracion = [h for h in report.legacy_component_findings if h.get("obrera") == "Integración"]
        # Hay rastro (info), pero ninguno de severidad de violacion.
        assert integracion
        assert all(h.get("severidad") == "info" for h in integracion)
        assert any("NO verificados" in h.get("descripcion", "") for h in integracion)
