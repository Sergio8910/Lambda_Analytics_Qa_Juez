from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AgentHttpResult:
    output: str
    error: Optional[str] = None
    infra_error: bool = False
    model_error: bool = False
    latency_ms: float = 0.0


class AgentHttpClient:
    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, timeout_ms: int = 10000):
        self.url = url
        self.headers = headers or {}
        self.timeout_ms = timeout_ms

    def call(self, payload: Dict[str, Any]) -> AgentHttpResult:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.headers}
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_ms / 1000.0) as resp:
                body = resp.read().decode("utf-8")
            latency_ms = (time.monotonic() - start) * 1000.0
            parsed = json.loads(body)
            output = (
                parsed.get("output")
                or parsed.get("agent_output")
                or parsed.get("response")
                or ""
            )
            return AgentHttpResult(output=output, latency_ms=latency_ms)
        except urllib.error.URLError as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            return AgentHttpResult(
                output="",
                error=str(exc),
                infra_error=True,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            return AgentHttpResult(
                output="",
                error=str(exc),
                model_error=True,
                latency_ms=latency_ms,
            )
