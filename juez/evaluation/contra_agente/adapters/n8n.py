"""Adapter n8n para el contra-agente.

Envia mensajes a un flujo n8n via webhook con historial de conversacion.

PAYLOAD REAL DE REFERENCIA: el payload "shotgun" de abajo (_build_payload)
asume que el flujo es un webhook conversacional simple que lee ALGUNA clave
de nivel superior (message/chatInput/text/...). Eso NO sirve para flujos
que esperan un sobre anidado especifico -- ej. un flujo de WhatsApp Business
API espera algo como entry[0].changes[0].value.messages[0].text.body, no una
clave plana. Para esos casos, `payload_template` permite adjuntar un ejemplo
REAL de ese sobre (con el texto del usuario reemplazado por un marcador) y el
adapter lo usa tal cual, sustituyendo el marcador en cada turno.
"""
from __future__ import annotations

import copy
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

# Marcador que el usuario deja en su payload_template donde debe ir el texto
# del turno actual. Reemplazo recursivo, funciona a cualquier profundidad de
# anidamiento (listas/dicts).
MARCADOR_MENSAJE = "{{JUEZ_MENSAJE}}"
MARCADOR_SESSION = "{{JUEZ_SESSION_ID}}"


def _sustituir_marcadores(nodo: Any, mensaje: str, session_id: str) -> Any:
    if isinstance(nodo, dict):
        return {k: _sustituir_marcadores(v, mensaje, session_id) for k, v in nodo.items()}
    if isinstance(nodo, list):
        return [_sustituir_marcadores(v, mensaje, session_id) for v in nodo]
    if isinstance(nodo, str):
        return nodo.replace(MARCADOR_MENSAJE, mensaje).replace(MARCADOR_SESSION, session_id)
    return nodo


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Fusiona `extra` sobre `base` de forma recursiva (extra tiene prioridad)."""
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class N8nAdapter:
    """Adapter para agentes n8n expuestos via webhook."""

    def __init__(
        self,
        webhook_url: str,
        auth_headers: Optional[Dict[str, str]] = None,
        timeout_s: float = 30.0,
        input_fields: Optional[List[str]] = None,
        agent_name: str = "",
        payload_template: Optional[Dict[str, Any]] = None,
        envelope_hint: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.auth_headers = auth_headers or {}
        self.timeout_s = timeout_s
        self.input_fields = _dedupe_fields(input_fields or [])
        self.agent_name = agent_name
        self.payload_template = payload_template
        # `envelope_hint`: sobre anidado INFERIDO del propio flujo n8n (ej.
        # {"body": {"message": "{{JUEZ_MENSAJE}}"}}). A diferencia de
        # `payload_template` (reemplazo total), esto se FUSIONA sobre el payload
        # shotgun para que el flujo reciba el texto en la ruta que realmente lee,
        # sin perder los alias planos. Solo aplica cuando no hay payload_template.
        self.envelope_hint = envelope_hint
        self.session_id = f"juez-{uuid.uuid4().hex[:12]}"
        self.last_debug: Dict[str, Any] = {}

    def send_message(self, message: str, history: List[Dict[str, str]]) -> tuple[str, float]:
        """Envia mensaje a n8n via webhook con historial como contexto.

        Retorna (agent_response, latency_ms).
        """
        recent_history = history[-6:] if len(history) > 6 else history
        if self.payload_template:
            payload = _sustituir_marcadores(copy.deepcopy(self.payload_template), message, self.session_id)
        else:
            payload = self._build_payload(message, recent_history)
        params = {
            "message": message,
            "chatInput": message,
            "sessionId": self.session_id,
        }
        headers = {"Content-Type": "application/json", **self.auth_headers}

        t0 = time.time()
        try:
            resp = requests.post(
                self.webhook_url,
                params=params,
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )
            latency_ms = (time.time() - t0) * 1000
            self.last_debug = {
                "url": self.webhook_url,
                "method": "POST",
                "status_code": resp.status_code,
                "latency_ms": round(latency_ms, 1),
                "payload_enviado": _redact_payload(payload),
                "query_enviado": params,
                "response_preview": (resp.text or "")[:800],
            }
            resp.raise_for_status()
            try:
                data = resp.json()
            except Exception:
                data = {"text": resp.text}

            text = (
                data.get("response")
                or data.get("message")
                or data.get("reply")
                or data.get("answer")
                or data.get("output")
                or data.get("text")
                or data.get("chatOutput")
                or str(data)
            )
            return str(text), latency_ms
        except requests.exceptions.Timeout:
            latency_ms = (time.time() - t0) * 1000
            self.last_debug = _error_debug(self.webhook_url, payload, params, latency_ms, f"Timeout >{self.timeout_s}s")
            return f"[ERROR: Timeout >{self.timeout_s}s]", latency_ms
        except requests.exceptions.ConnectionError as exc:
            latency_ms = (time.time() - t0) * 1000
            self.last_debug = _error_debug(self.webhook_url, payload, params, latency_ms, str(exc)[:300])
            return f"[ERROR: ConnectionError: {exc}]", latency_ms
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            if not self.last_debug:
                self.last_debug = _error_debug(self.webhook_url, payload, params, latency_ms, str(exc)[:300])
            return f"[ERROR: {exc}]", latency_ms

    def _build_payload(self, message: str, recent_history: List[Dict[str, str]]) -> Dict[str, Any]:
        """Construye un payload amplio para maximizar compatibilidad con n8n.

        Diferentes flujos esperan claves distintas (`message`, `chatInput`,
        `sessionId`, campos del negocio, etc.). En vez de mandar un contrato
        minimo, enviamos aliases comunes y campos inferidos para que el webhook
        pueda recorrer mas ramas sin que el usuario tenga que reconfigurar el flujo.
        """
        turn_number = max(1, len(recent_history) // 2 + 1)
        messages = [
            {
                "role": "assistant" if h.get("role") == "agent" else h.get("role", "user"),
                "content": h.get("content", ""),
            }
            for h in recent_history
        ]
        inferred = {
            field: _infer_field_value(field, message, recent_history)
            for field in self.input_fields
        }
        payload: Dict[str, Any] = {
            "message": message,
            "chatInput": message,
            "input": message,
            "text": message,
            "query": message,
            "question": message,
            "sessionId": self.session_id,
            "session_id": self.session_id,
            "conversationId": self.session_id,
            "conversation_id": self.session_id,
            "userId": "juez-qa",
            "user_id": "juez-qa",
            "history": recent_history,
            "messages": messages,
            "timestamp": datetime.now().isoformat(),
            "source": "lambda_juez_qa",
            "metadata": {
                "agent_name": self.agent_name,
                "turn": turn_number,
                "test_mode": "real",
                "generated_by": "Lambda Analytics Juez QA",
            },
            "juez_qa": {
                "agent_name": self.agent_name,
                "session_id": self.session_id,
                "turn": turn_number,
                "input_fields_inferred": list(inferred.keys()),
            },
        }
        payload.update(inferred)
        # Fusionar el sobre inferido del flujo (rutas anidadas que el flujo lee
        # de verdad, ej. body.message). Los marcadores se sustituyen aqui con el
        # mensaje/sesion del turno actual.
        if self.envelope_hint:
            envelope = _sustituir_marcadores(
                copy.deepcopy(self.envelope_hint), message, self.session_id
            )
            if isinstance(envelope, dict):
                _deep_merge(payload, envelope)
        return payload


def _dedupe_fields(fields: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in fields:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out[:30]


def _infer_field_value(field: str, message: str, history: List[Dict[str, str]]) -> str:
    f = field.lower()
    blob = " ".join([message] + [h.get("content", "") for h in history[-6:]])

    email = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", blob)
    phone = re.search(r"(?:\+?57\s*)?(3\d{2}[\s.-]?\d{3}[\s.-]?\d{4})", blob)
    date = re.search(r"\b(20\d{2}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/20\d{2})\b", blob)
    hour = re.search(r"\b(\d{1,2}:\d{2}\s*(?:am|pm)?)\b", blob, re.I)
    ref = re.search(r"\b(?:ref(?:erencia)?|pedido|orden|caso)\s*[:#-]?\s*([A-Z0-9-]{4,})\b", blob, re.I)
    address = re.search(r"\b(?:calle|carrera|cra|cll|avenida|av)\s+[^,.]{5,60}", blob, re.I)

    if "email" in f or "correo" in f:
        return email.group(0) if email else "qa.lambda@example.com"
    if any(k in f for k in ("phone", "telefono", "celular", "whatsapp", "mobile")):
        return phone.group(1).replace(" ", "").replace(".", "").replace("-", "") if phone else "3001234567"
    if any(k in f for k in ("nombre", "name", "cliente", "user")):
        return "Sergio QA"
    if any(k in f for k in ("ciudad", "city")):
        for city in ("Bogota", "Medellin", "Cali", "Barranquilla", "Bucaramanga"):
            if city.lower() in blob.lower():
                return city
        return "Bogota"
    if any(k in f for k in ("direccion", "address", "ubicacion")):
        return address.group(0) if address else "Calle 45 # 32-18"
    if any(k in f for k in ("fecha", "date", "dia")):
        return date.group(1) if date else "2026-07-10"
    if any(k in f for k in ("hora", "time")):
        return hour.group(1) if hour else "10:00"
    if any(k in f for k in ("id", "ref", "pedido", "orden", "caso")):
        return ref.group(1) if ref else "QA-77421"
    if any(k in f for k in ("mensaje", "message", "consulta", "descripcion", "comment")):
        return message
    return message[:180] or "dato de prueba QA"


def _redact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    redacted: Dict[str, Any] = {}
    sensitive = ("token", "key", "secret", "password", "authorization", "api_key")
    for key, value in payload.items():
        if any(s in key.lower() for s in sensitive):
            redacted[key] = "***"
        elif key in {"history", "messages"} and isinstance(value, list):
            redacted[key] = value[-4:]
        else:
            redacted[key] = value
    return redacted


def _error_debug(url: str, payload: Dict[str, Any], params: Dict[str, str], latency_ms: float, error: str) -> Dict[str, Any]:
    return {
        "url": url,
        "method": "POST",
        "status_code": None,
        "latency_ms": round(latency_ms, 1),
        "payload_enviado": _redact_payload(payload),
        "query_enviado": params,
        "response_preview": "",
        "error": error,
    }
