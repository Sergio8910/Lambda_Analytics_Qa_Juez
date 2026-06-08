"""Tests de la verificación SINTÉTICA de objetivos de flujos n8n.

Todo es sobre JSON estático: no se ejecuta ni se dispara nada.
"""
from __future__ import annotations

from juez.evaluation.n8n import Objective, verify_objectives


def _flujo_ticket_y_correo(
    *,
    incluir_email: bool = True,
    email_con_destinatario: bool = True,
    email_conectado: bool = True,
    email_habilitado: bool = True,
) -> dict:
    """Flujo: Webhook -> Crear Ticket (HTTP) -> Enviar Correo (Gmail)."""
    nodes = [
        {
            "id": "1",
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "parameters": {"path": "nuevo-caso"},
        },
        {
            "id": "2",
            "name": "Crear Ticket",
            "type": "n8n-nodes-base.httpRequest",
            "credentials": {"httpHeaderAuth": {"id": "c1", "name": "API"}},
            "parameters": {
                "method": "POST",
                "url": "https://soporte.example.com/api/tickets",
                "jsonBody": "={\"asunto\": \"{{$json.asunto}}\", \"ticket\": true}",
            },
        },
    ]
    connections: dict = {
        "Webhook": {"main": [[{"node": "Crear Ticket", "type": "main", "index": 0}]]},
    }

    if incluir_email:
        email_params: dict = {"subject": "Nuevo caso", "message": "Se creó el ticket"}
        if email_con_destinatario:
            email_params["sendTo"] = "soporte@example.com"
        email_node = {
            "id": "3",
            "name": "Enviar Correo",
            "type": "n8n-nodes-base.gmail",
            "parameters": email_params,
        }
        if email_habilitado is False:
            email_node["disabled"] = True
        if email_con_destinatario:
            email_node["credentials"] = {"gmailOAuth2": {"id": "c2", "name": "Gmail"}}
        nodes.append(email_node)
        if email_conectado:
            connections["Crear Ticket"] = {
                "main": [[{"node": "Enviar Correo", "type": "main", "index": 0}]]
            }

    return {"id": "wf-1", "name": "Flujo Soporte", "nodes": nodes, "connections": connections}


def _objetivos() -> list:
    return [
        Objective(id="crear_ticket", descripcion="Generar un ticket de soporte", kind="create_ticket"),
        Objective(id="enviar_correo", descripcion="Notificar por correo", kind="send_email"),
    ]


def test_flujo_completo_cumple():
    report = verify_objectives(_flujo_ticket_y_correo(), _objetivos())
    assert report.veredicto == "cumple"
    assert report.cumplidos == 2
    assert report.incumplidos == 0
    assert report.score_global == 100.0
    assert all(c.status == "cumplido" for c in report.objetivos)


def test_objetivo_sin_nodo_es_incumplido():
    report = verify_objectives(_flujo_ticket_y_correo(incluir_email=False), _objetivos())
    assert report.veredicto == "no_cumple"
    correo = next(c for c in report.objetivos if c.id == "enviar_correo")
    assert correo.status == "incumplido"
    assert correo.score == 0.0
    assert any("sin nodo" in f.title.lower() for f in correo.findings)
    # El ticket sí se cumple.
    ticket = next(c for c in report.objetivos if c.id == "crear_ticket")
    assert ticket.status == "cumplido"


def test_nodo_inalcanzable_es_incumplido():
    report = verify_objectives(
        _flujo_ticket_y_correo(email_conectado=False), _objetivos()
    )
    correo = next(c for c in report.objetivos if c.id == "enviar_correo")
    assert correo.status == "incumplido"
    assert any("inalcanzable" in f.title.lower() for f in correo.findings)


def test_nodo_deshabilitado_es_incumplido():
    report = verify_objectives(
        _flujo_ticket_y_correo(email_habilitado=False), _objetivos()
    )
    correo = next(c for c in report.objetivos if c.id == "enviar_correo")
    assert correo.status == "incumplido"
    assert any("deshabilitado" in f.title.lower() for f in correo.findings)


def test_correo_sin_destinatario_es_incumplido():
    report = verify_objectives(
        _flujo_ticket_y_correo(email_con_destinatario=False), _objetivos()
    )
    correo = next(c for c in report.objetivos if c.id == "enviar_correo")
    assert correo.status == "incumplido"
    assert any("destinatario" in f.title.lower() for f in correo.findings)


def test_objetivo_custom_por_param_contains():
    wf = _flujo_ticket_y_correo()
    obj = Objective(
        id="usa_post",
        descripcion="Debe hacer un POST",
        kind="custom",
        node_type_contains=["httprequest"],
        param_contains=["post"],
    )
    report = verify_objectives(wf, [obj])
    assert report.objetivos[0].status == "cumplido"


def test_http_request_sin_url_es_incumplido():
    wf = {
        "id": "wf-2",
        "name": "Sin URL",
        "nodes": [
            {"id": "1", "name": "Trigger", "type": "n8n-nodes-base.manualTrigger", "parameters": {}},
            {"id": "2", "name": "HTTP", "type": "n8n-nodes-base.httpRequest", "parameters": {"method": "GET"}},
        ],
        "connections": {"Trigger": {"main": [[{"node": "HTTP", "type": "main", "index": 0}]]}},
    }
    obj = Objective(id="llamar_api", kind="http_request")
    report = verify_objectives(wf, [obj])
    assert report.objetivos[0].status == "incumplido"
    assert any("url" in f.message.lower() for f in report.objetivos[0].findings)
