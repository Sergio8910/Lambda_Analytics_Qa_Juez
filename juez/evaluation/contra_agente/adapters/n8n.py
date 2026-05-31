"""Adapter n8n para el contra-agente.

Envía mensajes a un flujo n8n via webhook con historial de conversación.
"""
from __future__ import annotations

import time
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
    ) -> None:
        self.webhook_url = webhook_url
        self.auth_headers = auth_headers or {}
        self.timeout_s = timeout_s

    def send_message(self, message: str, history: List[Dict[str, str]]) -> tuple[str, float]:
        """Envía mensaje a n8n via webhook con historial como contexto.

        Retorna (agent_response, latency_ms).
        """
        recent_history = history[-6:] if len(history) > 6 else history

        payload = {
            "message": message,
            "history": recent_history,
            "timestamp": datetime.now().isoformat(),
        }
        headers = {"Content-Type": "application/json", **self.auth_headers}

        t0 = time.time()
        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )
            latency_ms = (time.time() - t0) * 1000
            resp.raise_for_status()
            data = resp.json()
            # n8n puede responder en diferentes formatos — intentar los comunes
            text = (
                data.get("response")
                or data.get("message")
                or data.get("output")
                or data.get("text")
                or str(data)
            )
            return str(text), latency_ms
        except requests.exceptions.Timeout:
            latency_ms = (time.time() - t0) * 1000
            return f"[ERROR: Timeout >{self.timeout_s}s]", latency_ms
        except requests.exceptions.ConnectionError as e:
            latency_ms = (time.time() - t0) * 1000
            return f"[ERROR: ConnectionError: {e}]", latency_ms
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            return f"[ERROR: {e}]", latency_ms
