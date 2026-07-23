"""Adapter Ordo (La Colmena) para el contra-agente.

A diferencia del N8nAdapter -- que apunta a un webhook CRUDO por agente y
autentica con headers arbitrarios ("por api key" del webhook) -- este adapter
conversa a traves de la PLATAFORMA Ordo ("por cuenta"):

- un unico endpoint conversacional (`POST /api/v1/chat/`),
- autenticado con una API key de CUENTA en el header `X-Api-Key`,
- el agente destino se elige por `server`/`project` (no por URL),
- flujo ASINCRONO: el POST responde 202 con un `conversation_id` y el estado
  se consulta por polling en `GET /api/v1/chat/<id>/` hasta `done=true`.

El estado de la conversacion lo mantiene Ordo por `conversation_id`, asi que
NO reenviamos el historial en cada turno: basta con reenviar el mismo
`conversation_id`. El `history` del contra-agente se ignora a proposito
(queda en la firma solo por compatibilidad con la interfaz del adapter).

La API key se toma de la variable de entorno `ORDO_API_KEY` por defecto para
no dejarla en el codigo; se puede pasar explicita al construir el adapter.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests

DEFAULT_BASE_URL = "https://ordo.lambdaanalytics.co"

# Reintentos ante fallos TRANSITORIOS del POST (timeout/conexion/5xx). Igual
# criterio que el N8nAdapter. Modulo-level para que los tests los bajen a 0.
_MAX_INTENTOS = 3
_BACKOFF_BASE_S = 0.5

# Polling del GET hasta que done=true.
_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 120.0

# Estados terminales de fallo que puede devolver Ordo.
_ESTADOS_FALLO = {"error", "failed", "cancelled", "canceled"}


class OrdoAdapter:
    """Adapter para agentes servidos por la plataforma Ordo (por cuenta)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        server: Optional[str] = None,
        project: Optional[str] = None,
        timeout_s: float = 30.0,
        poll_interval_s: float = _POLL_INTERVAL_S,
        poll_timeout_s: float = _POLL_TIMEOUT_S,
        agent_name: str = "",
    ) -> None:
        self.api_key = api_key or os.getenv("ORDO_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.server = server
        self.project = project
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self.poll_timeout_s = poll_timeout_s
        self.agent_name = agent_name
        # Estado de la conversacion: Ordo lo mantiene por conversation_id. Se
        # setea con el primer POST y se reenvia en los turnos siguientes.
        self.conversation_id: Optional[str] = None
        self.last_debug: Dict[str, Any] = {}

    @property
    def _chat_url(self) -> str:
        return f"{self.base_url}/api/v1/chat/"

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "X-Api-Key": self.api_key}

    def send_message(self, message: str, history: List[Dict[str, str]]) -> tuple[str, float]:
        """Envia un mensaje a Ordo y devuelve (reply, latency_ms).

        `history` se ignora: Ordo mantiene el hilo por conversation_id.
        """
        t0 = time.time()
        # 1. POST del mensaje (con reintentos ante fallos transitorios).
        conv_id, error = self._post_mensaje(message)
        if error is not None:
            latency_ms = (time.time() - t0) * 1000
            self.last_debug = _error_debug(self._chat_url, "POST", message, latency_ms, error)
            return f"[ERROR: {error}]", latency_ms

        self.conversation_id = conv_id
        # 2. Polling del GET hasta done=true (o fallo/timeout).
        reply, error = self._poll_reply(conv_id)
        latency_ms = (time.time() - t0) * 1000
        if error is not None:
            self.last_debug = _error_debug(
                f"{self._chat_url}{conv_id}/", "GET", message, latency_ms, error
            )
            return f"[ERROR: {error}]", latency_ms

        self.last_debug = {
            "url": self._chat_url,
            "method": "POST+GET",
            "status_code": 200,
            "latency_ms": round(latency_ms, 1),
            "conversation_id": conv_id,
            "payload_enviado": {"message": message, "server": self.server, "project": self.project},
            "response_preview": (reply or "")[:800],
        }
        return str(reply), latency_ms

    def _post_mensaje(self, message: str) -> tuple[Optional[str], Optional[str]]:
        """Manda el POST y devuelve (conversation_id, None) o (None, error)."""
        body: Dict[str, Any] = {"message": message}
        if self.conversation_id:
            body["conversation_id"] = self.conversation_id
        else:
            # Solo en el primer turno indicamos el destino.
            if self.server:
                body["server"] = self.server
            if self.project:
                body["project"] = self.project

        ultimo_error = "error desconocido"
        for intento in range(1, _MAX_INTENTOS + 1):
            try:
                resp = requests.post(
                    self._chat_url, json=body, headers=self._headers(), timeout=self.timeout_s
                )
                if resp.status_code >= 500 and intento < _MAX_INTENTOS:
                    ultimo_error = f"HTTP {resp.status_code}"
                    time.sleep(_BACKOFF_BASE_S * intento)
                    continue
                resp.raise_for_status()
                data = _json_seguro(resp)
                conv_id = (
                    self.conversation_id
                    or data.get("conversation_id")
                    or data.get("conversationId")
                )
                if not conv_id:
                    return None, "respuesta sin conversation_id"
                return str(conv_id), None
            except requests.exceptions.Timeout:
                ultimo_error = f"Timeout >{self.timeout_s}s"
            except requests.exceptions.ConnectionError as exc:
                ultimo_error = f"ConnectionError: {str(exc)[:200]}"
            except Exception as exc:
                # 4xx u otro no transitorio: no reintentar.
                return None, str(exc)[:200]
            if intento < _MAX_INTENTOS:
                time.sleep(_BACKOFF_BASE_S * intento)
        return None, ultimo_error

    def _poll_reply(self, conv_id: str) -> tuple[Optional[str], Optional[str]]:
        """Hace polling del GET hasta done=true. Devuelve (reply, None) o (None, error)."""
        url = f"{self._chat_url}{conv_id}/"
        deadline = time.time() + self.poll_timeout_s
        while True:
            try:
                resp = requests.get(url, headers=self._headers(), timeout=self.timeout_s)
                resp.raise_for_status()
                data = _json_seguro(resp)
            except Exception as exc:
                return None, f"polling fallido: {str(exc)[:200]}"

            status = str(data.get("status", "")).lower()
            if data.get("done") is True or status == "success":
                reply = data.get("reply") or data.get("message") or data.get("output") or ""
                return str(reply), None
            if status in _ESTADOS_FALLO:
                detalle = data.get("error") or data.get("reply") or status
                return None, f"conversacion en estado '{status}': {str(detalle)[:200]}"

            if time.time() >= deadline:
                return None, f"polling timeout >{self.poll_timeout_s}s (ultimo estado: {status or 'desconocido'})"
            time.sleep(self.poll_interval_s)


def _json_seguro(resp: "requests.Response") -> Dict[str, Any]:
    try:
        data = resp.json()
    except Exception:
        return {"reply": resp.text}
    return data if isinstance(data, dict) else {"reply": str(data)}


def _error_debug(url: str, method: str, message: str, latency_ms: float, error: str) -> Dict[str, Any]:
    return {
        "url": url,
        "method": method,
        "status_code": None,
        "latency_ms": round(latency_ms, 1),
        "payload_enviado": {"message": message},
        "response_preview": "",
        "error": error,
    }
