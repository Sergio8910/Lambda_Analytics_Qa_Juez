"""Adapter n8n para el contra-agente.

Envia mensajes a un flujo n8n via webhook con historial de conversacion.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests


class N8nAdapter:
    """Adapter para agentes n8n expuestos via webhook."""

    def __init__(
        self,
        webhook_url: str,
        auth_headers: Optional[Dict[str, str]] = None,
        timeout_s: float = 30.0,
        input_fields: Optional[List[str]] = None,
        agent_name: str = "",
    ) -> None:
        self.webhook_url = webhook_url
        self.auth_headers = auth_headers or {}
        self.timeout_s = timeout_s
        self.input_fields = _dedupe_fields(input_fields or [])
        self.agent_name = agent_name
        self.session_id = f"juez-{uuid.uuid4().hex[:12]}"
        self.last_debug: Dict[str, Any] = {}

    def send_message(self, message: str, history: List[Dict[str, str]]) -> tuple[str, float]:
        """Envia mensaje a n8n via webhook con historial como contexto.

        Retorna (agent_response, latency_ms).
        """
        recent_history = history[-6:] if len(history) > 6 else history
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
