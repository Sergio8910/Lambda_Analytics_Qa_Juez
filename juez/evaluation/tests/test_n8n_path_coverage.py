"""Cobertura de caminos n8n: enumerar caminos, extraer condiciones de cada
rama y clasificar si la rama es controlable desde el payload inicial o depende
de la ejecución (salida de un HTTP/Code/AI previo).
"""
from __future__ import annotations

from juez.evaluation.n8n.path_coverage import (
    _campo_de_expresion,
    analizar_caminos,
    cobertura_de_nodos,
    sintetizar_inputs_por_camino,
)


def _wf(nodes, connections):
    return {"name": "wf", "nodes": nodes, "connections": connections}


def _if_node(name, left, right, op=("string", "equals")):
    return {
        "name": name, "type": "n8n-nodes-base.if", "id": name,
        "parameters": {"conditions": {"conditions": [
            {"leftValue": left, "rightValue": right, "operator": {"type": op[0], "operation": op[1]}}
        ]}},
    }


def test_extrae_campo_de_input():
    assert _campo_de_expresion("={{ $json.tipo }}") == ("input", "tipo")
    assert _campo_de_expresion("={{ $json.output.clasificacion }}") == ("input", "output.clasificacion")


def test_extrae_campo_de_nodo_previo():
    fuente, campo = _campo_de_expresion("={{ $('Bandera').last().json.estado }}")
    assert fuente == "nodo:Bandera"
    assert campo == "estado"


def test_rama_directa_al_webhook_es_controlable_por_input():
    """IF justo después del webhook sobre $json.tipo -> controlable por input."""
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        _if_node("If", "={{ $json.tipo }}", "premium"),
        {"name": "Premium", "type": "n8n-nodes-base.set", "id": "p", "parameters": {}},
        {"name": "Normal", "type": "n8n-nodes-base.set", "id": "n", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "Premium", "type": "main", "index": 0}],   # output 0 = true
            [{"node": "Normal", "type": "main", "index": 0}],    # output 1 = false
        ]},
    }
    r = analizar_caminos(_wf(nodes, conns))
    assert "If" in r["nodos_de_ramificacion"]
    assert r["ramas_resumen"]["controlable_por_input"] >= 1
    rama_true = next(d for d in r["ramas"] if d["nodo"] == "If" and d["rama"] == "true")
    assert rama_true["controlabilidad"] == "controlable_por_input"
    assert rama_true["condiciones"][0]["campo"] == "tipo"
    assert rama_true["condiciones"][0]["valor"] == "premium"


def test_rama_tras_http_depende_de_ejecucion():
    """IF sobre $json.statusCode DESPUÉS de un HTTP -> depende de ejecución
    (el statusCode lo produce el HTTP, no el payload inicial)."""
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        {"name": "HTTP", "type": "n8n-nodes-base.httpRequest", "id": "h", "parameters": {"url": "https://api.x"}},
        _if_node("If", "={{ $json.statusCode }}", 200, ("number", "equals")),
        {"name": "Ok", "type": "n8n-nodes-base.set", "id": "o", "parameters": {}},
        {"name": "Err", "type": "n8n-nodes-base.set", "id": "e", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]},
        "HTTP": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "Ok", "type": "main", "index": 0}],
            [{"node": "Err", "type": "main", "index": 0}],
        ]},
    }
    r = analizar_caminos(_wf(nodes, conns))
    rama_true = next(d for d in r["ramas"] if d["nodo"] == "If" and d["rama"] == "true")
    assert rama_true["controlabilidad"] == "depende_de_ejecucion"


def test_dos_caminos_por_el_if():
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        _if_node("If", "={{ $json.tipo }}", "premium"),
        {"name": "A", "type": "n8n-nodes-base.set", "id": "a", "parameters": {}},
        {"name": "B", "type": "n8n-nodes-base.set", "id": "b", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "A", "type": "main", "index": 0}],
            [{"node": "B", "type": "main", "index": 0}],
        ]},
    }
    r = analizar_caminos(_wf(nodes, conns))
    assert r["total_caminos"] == 2
    hojas = {c["secuencia"][-1] for c in r["caminos"]}
    assert hojas == {"A", "B"}


def test_switch_con_fallback():
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        {"name": "Sw", "type": "n8n-nodes-base.switch", "id": "s", "parameters": {
            "rules": {"values": [
                {"conditions": {"conditions": [
                    {"leftValue": "={{ $json.accion }}", "rightValue": "crear",
                     "operator": {"type": "string", "operation": "equals"}}]},
                 "outputKey": "Crear"},
            ]},
            "options": {"fallbackOutput": "extra", "renameFallbackOutput": "Otro"},
        }},
        {"name": "Crear", "type": "n8n-nodes-base.set", "id": "c", "parameters": {}},
        {"name": "Otro", "type": "n8n-nodes-base.set", "id": "o", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "Sw", "type": "main", "index": 0}]]},
        "Sw": {"main": [
            [{"node": "Crear", "type": "main", "index": 0}],
            [{"node": "Otro", "type": "main", "index": 0}],
        ]},
    }
    r = analizar_caminos(_wf(nodes, conns))
    etiquetas = {d["rama"] for d in r["ramas"] if d["nodo"] == "Sw"}
    assert "Crear" in etiquetas and "Otro" in etiquetas
    fb = next(d for d in r["ramas"] if d["nodo"] == "Sw" and d["rama"] == "Otro")
    assert fb["controlabilidad"] == "fallback"


def test_sintesis_fuerza_true_y_false_del_if():
    """El payload del camino true satisface la condición; el del false la niega."""
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        _if_node("If", "={{ $json.tipo }}", "premium"),
        {"name": "A", "type": "n8n-nodes-base.set", "id": "a", "parameters": {}},
        {"name": "B", "type": "n8n-nodes-base.set", "id": "b", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "A", "type": "main", "index": 0}],
            [{"node": "B", "type": "main", "index": 0}],
        ]},
    }
    r = sintetizar_inputs_por_camino(_wf(nodes, conns))
    assert r["caminos_cubribles_solo_con_input"] == 2
    por_hoja = {i["secuencia"][-1]: i["payload_sugerido"] for i in r["inputs_por_camino"]}
    assert por_hoja["A"] == {"tipo": "premium"}       # rama true: satisface
    assert por_hoja["B"]["tipo"] != "premium"         # rama false: niega


def test_sintesis_declara_ramas_no_forzables_tras_http():
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        {"name": "HTTP", "type": "n8n-nodes-base.httpRequest", "id": "h", "parameters": {"url": "https://api.x"}},
        _if_node("If", "={{ $json.statusCode }}", 200, ("number", "equals")),
        {"name": "Ok", "type": "n8n-nodes-base.set", "id": "o", "parameters": {}},
        {"name": "Err", "type": "n8n-nodes-base.set", "id": "e", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]},
        "HTTP": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "Ok", "type": "main", "index": 0}],
            [{"node": "Err", "type": "main", "index": 0}],
        ]},
    }
    r = sintetizar_inputs_por_camino(_wf(nodes, conns))
    assert r["caminos_que_requieren_datos_de_ejecucion"] == 2
    for i in r["inputs_por_camino"]:
        assert i["ramas_no_forzables_desde_input"]  # el statusCode viene del HTTP, no del input
        assert i["totalmente_cubrible_por_input"] is False


def test_cobertura_de_nodos_flujo_controlable_100pct():
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        _if_node("If", "={{ $json.tipo }}", "premium"),
        {"name": "A", "type": "n8n-nodes-base.set", "id": "a", "parameters": {}},
        {"name": "B", "type": "n8n-nodes-base.set", "id": "b", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "A", "type": "main", "index": 0}],
            [{"node": "B", "type": "main", "index": 0}],
        ]},
    }
    c = cobertura_de_nodos(_wf(nodes, conns))
    assert c["porcentaje_cubrible_por_input"] == 100.0
    assert set(c["nodos_cubribles_por_input"]) == {"Webhook", "If", "A", "B"}
    assert c["nodos_que_requieren_datos_de_ejecucion"] == []


def test_cobertura_de_nodos_flujo_gated_por_http_requiere_ejecucion():
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        {"name": "HTTP", "type": "n8n-nodes-base.httpRequest", "id": "h", "parameters": {"url": "https://api.x"}},
        _if_node("If", "={{ $json.statusCode }}", 200, ("number", "equals")),
        {"name": "Ok", "type": "n8n-nodes-base.set", "id": "o", "parameters": {}},
        {"name": "Err", "type": "n8n-nodes-base.set", "id": "e", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]},
        "HTTP": {"main": [[{"node": "If", "type": "main", "index": 0}]]},
        "If": {"main": [
            [{"node": "Ok", "type": "main", "index": 0}],
            [{"node": "Err", "type": "main", "index": 0}],
        ]},
    }
    c = cobertura_de_nodos(_wf(nodes, conns))
    # Ok/Err quedan tras un IF gated por el statusCode del HTTP -> requieren ejecución.
    assert "Ok" in c["nodos_que_requieren_datos_de_ejecucion"]
    assert "Err" in c["nodos_que_requieren_datos_de_ejecucion"]
    assert c["porcentaje_cubrible_por_input"] < 100.0


def test_no_explota_con_ciclos():
    """Un ciclo no debe colgar el DFS (n8n permite loops)."""
    nodes = [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "id": "w", "parameters": {"path": "x"}},
        {"name": "A", "type": "n8n-nodes-base.set", "id": "a", "parameters": {}},
        {"name": "B", "type": "n8n-nodes-base.set", "id": "b", "parameters": {}},
    ]
    conns = {
        "Webhook": {"main": [[{"node": "A", "type": "main", "index": 0}]]},
        "A": {"main": [[{"node": "B", "type": "main", "index": 0}]]},
        "B": {"main": [[{"node": "A", "type": "main", "index": 0}]]},  # ciclo A<->B
    }
    r = analizar_caminos(_wf(nodes, conns))
    assert r["total_caminos"] >= 1
    assert any(c.get("cierra_en_ciclo") for c in r["caminos"])
