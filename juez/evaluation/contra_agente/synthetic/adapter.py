"""MockAdapter — interfaz compatible con N8nAdapter pero sin tocar webhook.

Reusa la firma `send_message(message, history) -> (text, latency_ms)` que
espera el `ConversationWorker`. Internamente delega a `MockAgent` que tiene
function calling habilitado contra el `MockToolRunner`.

El historial del adapter es para compatibilidad de firma; el MockAgent
mantiene su propio historial internamente (no se necesita sincronizar).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Tuple

from .mock_agent import MockAgent
from .mock_tools import MockToolRunner

log = logging.getLogger("juez.synthetic.adapter")


class MockAdapter:
    """Adapter sintético compatible con la interfaz del N8nAdapter."""

    def __init__(self, agent: MockAgent, tool_runner: MockToolRunner) -> None:
        self.agent = agent
        self.tool_runner = tool_runner

    def send_message(self, message: str, history: List[Dict[str, str]]) -> Tuple[str, float]:
        """Compatible con N8nAdapter.send_message. `history` se ignora porque
        el MockAgent ya acumula su propio historial."""
        t0 = time.time()
        try:
            text = self.agent.respond(message, self.tool_runner)
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            log.exception("mock_adapter error: %s", exc)
            return f"[ERROR mock_adapter: {type(exc).__name__}: {exc}]", latency_ms
        latency_ms = (time.time() - t0) * 1000
        return text, latency_ms
