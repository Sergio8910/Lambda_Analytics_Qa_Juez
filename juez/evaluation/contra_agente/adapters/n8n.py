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

# Reintentos ante fallos transitorios (timeout/conexion/5xx). Modulo-level para
# que los tests puedan bajarlos a 0 y no dormir.
_MAX_INTENTOS = 3
_BACKOFF_BASE_S = 0.5
# Reintentos de AUTO-SANADO: cuando un nodo del flujo rechaza el payload por un
# dato faltante/invalido (4xx tipo "Missing X"), el contra-agente extrae el campo,
# lo rellena con un valor valido y reintenta, para que la prueba recorra el flujo
# completo en vez de quedar "mocha". Acotado para no ciclar.
_MAX_HEAL = 3

# Palabras que NO son nombres de campo (se descartan al extraer del mensaje).
_STOPWORDS_CAMPO = {
    "error", "request", "parameter", "parameters", "body", "field", "fields",
    "value", "values", "the", "a", "an", "is", "are", "was", "el", "la", "los",
    "las", "un", "una", "de", "del", "in", "with", "para", "por", "que", "please",
    "check", "your", "missing", "required", "invalid", "bad", "json", "input",
}


def _campos_faltantes(text: str) -> List[str]:
    """Extrae nombres de campo que el nodo dice faltar/estar mal, del cuerpo de
    un error 4xx (ej. 'Missing transcription' -> ['transcription'])."""
    if not text:
        return []
    campos: List[str] = []
    patrones = [
        r"missing\s+[\"']?([A-Za-z_][\w]*)",
        r"falta[n]?\s+(?:el |la |los |las )?[\"']?([A-Za-z_][\w]*)",
        r"[\"']?([A-Za-z_][\w]*)[\"']?\s+is\s+required",
        r"required[:\s]+[\"']?([A-Za-z_][\w]*)",
        r"requiere[n]?\s+(?:el |la |un |una )?[\"']?([A-Za-z_][\w]*)",
        r"invalid\s+parameter[:\s]+[\"']?([A-Za-z_][\w]*)",
        r"campo\s+[\"']?([A-Za-z_][\w]*)[\"']?\s+(?:es\s+)?(?:obligatorio|requerido|faltante|vac[ií]o)",
        r"[\"']?([A-Za-z_][\w]*)[\"']?\s+(?:no puede|cannot) (?:estar|be)\s+(?:vac[ií]o|empty|null)",
    ]
    for pat in patrones:
        for m in re.finditer(pat, text, re.I):
            campo = m.group(1).strip()
            if campo and campo.lower() not in _STOPWORDS_CAMPO and len(campo) > 1:
                campos.append(campo)
    # dedupe conservando orden
    out, seen = [], set()
    for c in campos:
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


# Reintentos ante el error de MEMORIA de chat del AI Agent (mensaje role='tool'
# sin un 'tool_calls' previo). Se reintenta con una SESION NUEVA (memoria limpia)
# para intentar pasar el nodo; si persiste, se reporta con "como solucionar".
_MAX_MEM_RETRY = 2

_MSG_MEMORIA = (
    "nodo AI Agent / modelo de lenguaje: memoria de chat inconsistente "
    "(mensaje role='tool' sin un 'tool_calls' previo). Suele pasar en "
    "conversaciones multi-turno con herramientas: el nodo de memoria "
    "(p.ej. Postgres Chat Memory) persiste el ciclo de herramienta incompleto y "
    "al recargarlo el historial queda invalido. Como solucionar: limpiar el "
    "historial de la sesion antes de la prueba, o ajustar el nodo de memoria para "
    "no guardar mensajes 'tool' sueltos (o desactivar memoria si el flujo no la usa)."
)


def _es_error_memoria(text: str) -> bool:
    """Detecta el error de OpenAI por historial de chat invalido con herramientas."""
    low = (text or "").lower()
    return "tool" in low and ("tool_calls" in low or "preceding message" in low or "preceeding message" in low)


_FLOW_SIGNALS = (
    "cannot read propert", "is not a function", "is not defined", "undefined",
    "typeerror", "referenceerror", "syntaxerror", "nameerror", "econnrefused",
    "etimedout", "unauthorized", "forbidden", "credential", "tool_calls",
    "no output data", "authentication", "permission",
)


def _clasificar_error(status: int, text: str, campos: List[str]) -> str:
    """'datos' = el flujo rechazo el payload (arreglable con mejores datos);
    'flujo' = fallo del propio flujo/infra (se reporta, no se reintenta)."""
    low = (text or "").lower()
    if any(sig in low for sig in _FLOW_SIGNALS):
        return "flujo"
    if campos:
        return "datos"
    # 4xx sin campo identificable: probablemente datos, pero no auto-sanable.
    return "datos" if 400 <= status < 500 else "flujo"


def _detalle_error(text: str, status: int) -> str:
    """Mensaje humano corto del error (intenta leer {'error': ...} del cuerpo)."""
    import json as _json
    if text:
        try:
            data = _json.loads(text)
            if isinstance(data, dict):
                for k in ("error", "message", "detail", "msg"):
                    if isinstance(data.get(k), str) and data[k].strip():
                        return data[k].strip()[:200]
        except Exception:
            pass
        return text.strip()[:200]
    return f"HTTP {status}"


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

        # Reintentos ante fallos TRANSITORIOS (timeout, error de conexion, 5xx):
        # un blip pasajero en un turno intermedio envenenaba la conversacion
        # entera (el "[ERROR:...]" quedaba en el history y arrastraba los turnos
        # siguientes). Un 4xx NO se reintenta: es error de cliente, no se
        # arregla solo. Tras agotar intentos, devuelve el error (last_debug ya
        # refleja el fallo, y el worker lo trata como fallo de transporte).
        ultimo_error = "error desconocido"
        transient_left = _MAX_INTENTOS - 1   # reintentos por fallo transitorio (timeout/conexion/5xx)
        heal_left = _MAX_HEAL                 # reintentos por auto-sanado de datos (4xx)
        mem_left = _MAX_MEM_RETRY             # reintentos por error de memoria (sesion nueva)
        healed: set[str] = set()
        latency_ms = 0.0
        while True:
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
                # Error de MEMORIA de chat (role 'tool' sin 'tool_calls'): puede
                # venir en 4xx o 5xx segun como el flujo envuelva el error de OpenAI.
                # Se reintenta con SESION NUEVA (memoria limpia) para pasar el nodo.
                if resp.status_code >= 400 and _es_error_memoria(resp.text):
                    if mem_left > 0:
                        mem_left -= 1
                        self.session_id = f"juez-{uuid.uuid4().hex[:12]}"
                        recent_history = []  # sesion nueva => historial limpio
                        payload = (
                            _sustituir_marcadores(copy.deepcopy(self.payload_template), message, self.session_id)
                            if self.payload_template else self._build_payload(message, recent_history)
                        )
                        params = {"message": message, "chatInput": message, "sessionId": self.session_id}
                        self.last_debug["auto_sanado_memoria"] = {"nueva_sesion": self.session_id}
                        time.sleep(_BACKOFF_BASE_S)
                        continue
                    self.last_debug["error_class"] = "flujo"
                    return f"[ERROR flujo: {_MSG_MEMORIA}]", latency_ms
                # 5xx: fallo transitorio del servidor -> reintentar.
                if resp.status_code >= 500:
                    if transient_left > 0:
                        transient_left -= 1
                        ultimo_error = f"HTTP {resp.status_code}"
                        time.sleep(_BACKOFF_BASE_S)
                        continue
                    self.last_debug["error_class"] = "flujo"
                    return f"[ERROR flujo: HTTP {resp.status_code}]", latency_ms
                # 4xx: el flujo rechazo el payload. Auto-sanar si es por DATOS
                # (campo faltante/invalido) y quedan reintentos; si es del FLUJO
                # o no se puede sanar, se reporta con su clasificacion.
                if 400 <= resp.status_code < 500:
                    campos = [c for c in _campos_faltantes(resp.text) if c.lower() not in healed]
                    clasif = _clasificar_error(resp.status_code, resp.text, campos)
                    if clasif == "datos" and campos and heal_left > 0:
                        heal_left -= 1
                        for c in campos:
                            val = _infer_field_value(c, message, recent_history)
                            payload[c] = val
                            if isinstance(payload.get("body"), dict):
                                payload["body"][c] = val
                            healed.add(c.lower())
                        self.last_debug["auto_sanado"] = {
                            "campos_agregados": sorted(healed),
                            "razon": _detalle_error(resp.text, resp.status_code),
                        }
                        time.sleep(_BACKOFF_BASE_S)
                        continue
                    detalle = _detalle_error(resp.text, resp.status_code)
                    self.last_debug["error_class"] = clasif
                    if healed:
                        self.last_debug["auto_sanado_campos"] = sorted(healed)
                    return f"[ERROR {clasif}: {detalle}]", latency_ms
                # 2xx/3xx: exito.
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
                ultimo_error = f"Timeout >{self.timeout_s}s"
                self.last_debug = _error_debug(self.webhook_url, payload, params, latency_ms, ultimo_error)
            except requests.exceptions.ConnectionError as exc:
                latency_ms = (time.time() - t0) * 1000
                ultimo_error = f"ConnectionError: {exc}"
                self.last_debug = _error_debug(self.webhook_url, payload, params, latency_ms, str(exc)[:300])
            except Exception as exc:
                latency_ms = (time.time() - t0) * 1000
                if not self.last_debug:
                    self.last_debug = _error_debug(self.webhook_url, payload, params, latency_ms, str(exc)[:300])
                return f"[ERROR flujo: {exc}]", latency_ms
            # Fallo transitorio (timeout/conexion): reintentar si quedan intentos.
            if transient_left > 0:
                transient_left -= 1
                time.sleep(_BACKOFF_BASE_S)
                continue
            return f"[ERROR: {ultimo_error}]", latency_ms

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
    if any(k in f for k in ("phone", "telefono", "celular", "whatsapp", "mobile", "numero", "number", "waid", "msisdn")):
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
