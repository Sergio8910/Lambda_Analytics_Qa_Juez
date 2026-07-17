"""CodeNodeWorker: analiza el codigo real dentro de los Code/Function nodes de
n8n. Cierra un punto ciego real -- antes las obreras solo veian el prompt del
agente, asi que una fuga de datos o un exec peligroso escrito en un Function
node era invisible para el analisis de seguridad.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from juez.colmena.scanner import scan_project
from juez.colmena.workers import CodeNodeWorker, FindingBuilder


def _findings(nodes: list[dict]) -> list:
    wf = {"name": "flujo", "nodes": nodes, "connections": {}}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "flujo.json").write_text(json.dumps(wf), encoding="utf-8")
        inv = scan_project(root)
        return CodeNodeWorker(root, inv, FindingBuilder()).run()


def _code_node(nombre: str, code: str, key: str = "jsCode") -> dict:
    return {"name": nombre, "type": "n8n-nodes-base.code", "parameters": {key: code}}


def _titulos(findings) -> str:
    return " || ".join(f.title.lower() for f in findings)


def test_detecta_exec_dinamico_como_critico():
    fs = _findings([_code_node("X", 'const cp = require("child_process"); cp.execSync(userInput);')])
    assert any(f.severity == "critical" and "ejecucion dinamica" in f.title.lower() for f in fs)


def test_detecta_eval_en_python():
    fs = _findings([_code_node("X", "resultado = eval(entrada_usuario)", key="pythonCode")])
    assert any(f.severity == "critical" for f in fs)


def test_detecta_subprocess_python():
    fs = _findings([_code_node("X", "import subprocess; subprocess.run(cmd, shell=True)", key="pythonCode")])
    assert any(f.severity == "critical" for f in fs)


def test_detecta_select_sin_where_como_fuga():
    fs = _findings([_code_node("Fuga", 'const r = await db.query("SELECT * FROM huespedes"); return r;')])
    assert any(f.severity == "high" and "sin filtro" in f.title.lower() for f in fs)


def test_select_con_where_no_se_marca():
    """Un SELECT filtrado por el dueno del contexto NO es fuga -- no debe gritar."""
    fs = _findings([_code_node("OK", 'const r = await db.query("SELECT nombre FROM huespedes WHERE huesped_id = $1", [id]);')])
    assert not any("sin filtro" in f.title.lower() for f in fs)


def test_detecta_ssrf_por_interpolacion():
    fs = _findings([_code_node("SSRF", 'const r = await fetch(`http://interno/${$json.host}`);')])
    assert any(f.severity == "medium" and "ssrf" in f.title.lower() for f in fs)


def test_http_con_url_fija_no_es_ssrf():
    fs = _findings([_code_node("OK", 'const r = await fetch("https://api.example.com/v1/ping");')])
    assert not any("ssrf" in f.title.lower() for f in fs)


def test_code_node_sano_no_genera_hallazgos():
    fs = _findings([_code_node("Sano", "return items.map(i => ({ json: { ok: true } }));")])
    assert fs == []


def test_atribuye_el_hallazgo_al_nodo_correcto():
    fs = _findings([
        _code_node("NodoSeguro", "return items;"),
        _code_node("NodoPeligroso", "eval(x)"),
    ])
    criticos = [f for f in fs if f.severity == "critical"]
    assert criticos and "NodoPeligroso" in criticos[0].description


def test_ignora_nodos_que_no_son_code():
    wf_nodes = [{"name": "Webhook", "type": "n8n-nodes-base.webhook", "parameters": {"path": "x"}}]
    assert _findings(wf_nodes) == []
