"""Driver sintetico: dispara un flujo n8n por su webhook con un payload ficticio.

A diferencia del N8nAdapter del contra-agente (que envuelve un 'message' de chat),
este envia el payload sintetico crudo directamente al webhook del (sub)flujo —
disparando la logica "por debajo" sin activar el canal real (Telegram, etc.).
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import requests

from ..registry import driver


@driver("n8n_webhook")
class N8nWebhookDriver:
    def __init__(
        self,
        webhook_url: str = "",
        webhook_path: str = "",
        base_url: str = "",
        use_test_webhook: bool = False,
        auth_headers: Optional[Dict[str, str]] = None,
        timeout_s: float = 120.0,
    ) -> None:
        base = (base_url or os.getenv("N8N_BASE_URL", "")).rstrip("/")
        if webhook_url:
            self.webhook_url = webhook_url
        elif webhook_path:
            segmento = "webhook-test" if use_test_webhook else "webhook"
            self.webhook_url = f"{base}/{segmento}/{webhook_path.lstrip('/')}"
        else:
            raise ValueError("N8nWebhookDriver requiere 'webhook_url' o 'webhook_path'")
        self.auth_headers = auth_headers or {}
        self.timeout_s = timeout_s

    def trigger(self, synthetic_input: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json", **self.auth_headers}
        t0 = time.time()
        try:
            resp = requests.post(
                self.webhook_url, json=synthetic_input,
                headers=headers, timeout=self.timeout_s,
            )
            latency_ms = (time.time() - t0) * 1000
            try:
                body: Any = resp.json()
            except Exception:
                body = resp.text
            return {
                "ok": resp.ok,
                "http_status": resp.status_code,
                "response": body,
                "latency_ms": round(latency_ms, 1),
                "error": None,
                "url": self.webhook_url,
            }
        except requests.exceptions.Timeout:
            return {"ok": False, "http_status": None, "response": None,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "error": f"Timeout >{self.timeout_s}s", "url": self.webhook_url}
        except Exception as exc:
            return {"ok": False, "http_status": None, "response": None,
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "error": str(exc), "url": self.webhook_url}
