"""Adapter ElevenLabs para el contra-agente."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from juez.evaluation.contracts import RunnerResult
from juez.evaluation.report_models import TestCase


class ElevenLabsAdapter:
    """Adapter para agentes ElevenLabs ConvAI."""

    def __init__(
        self,
        agent_id: str,
        analisis: Dict[str, Any],
        openai_key: str,
        el_key: str = "",
        modo_ejecucion: str = "real",
    ) -> None:
        self.agent_id = agent_id
        self.analisis = analisis
        self.openai_key = openai_key
        self.el_key = el_key
        self.modo_ejecucion = modo_ejecucion
        self._real_session = None
        self.last_debug: Dict[str, Any] = {}
        self._runner = self._build_runner()

    def _build_runner(self):
        if self.modo_ejecucion == "sandbox":
            return self._build_sandbox_runner()
        if self.modo_ejecucion == "real":
            return self._build_real_convai_runner()
        try:
            from juez.evaluation.adapters.tools_runner import crear_runner_con_tools_reales
            return crear_runner_con_tools_reales(
                self.analisis, self.agent_id, self.openai_key, self.el_key
            )
        except Exception:
            return self._build_direct_runner()

    def _build_sandbox_runner(self):
        """Runner sin side effects: no abre ElevenLabs ni ejecuta webhooks reales."""
        from juez.evaluation.contra_agente.synthetic.adapter import MockAdapter
        from juez.evaluation.contra_agente.synthetic.mock_agent import MockAgent
        from juez.evaluation.contra_agente.synthetic.mock_tools import MockToolRunner
        from juez.evaluation.contra_agente.synthetic.snapshot_factory import make_synthetic_data

        _, canonical = make_synthetic_data(f"elevenlabs_{self.agent_id}", 1)
        agent = MockAgent(
            system_prompt=self.analisis.get("prompt", {}).get("completo", ""),
            herramientas=self.analisis.get("tools", []) or self.analisis.get("herramientas", []),
            model=self.analisis.get("metricas", {}).get("modelo_llm") or "gpt-4o-mini",
            openai_key=self.openai_key,
        )
        adapter = MockAdapter(agent=agent, tool_runner=MockToolRunner(canonical))

        def runner(tc: TestCase) -> RunnerResult:
            text, latency_ms = adapter.send_message(
                tc.input,
                [
                    {"role": line.split(": ", 1)[0], "content": line.split(": ", 1)[1]}
                    for line in (tc.context or [])
                    if ": " in line
                ],
            )
            return RunnerResult(output_text=text, retrieval_context=[], latency_ms=latency_ms)

        return runner

    def _build_real_convai_runner(self):
        """Fallback real de un solo turno si no se puede usar sesion persistente."""

        def runner(tc: TestCase) -> RunnerResult:
            if not self.el_key:
                return RunnerResult(
                    output_text="",
                    retrieval_context=[],
                    latency_ms=0.0,
                    error="ELEVENLABS_API_KEY no configurada",
                )
            try:
                from juez.evaluation.adapters.elevenlabs_adapter import llamar_agente

                context = "\n".join(tc.context or [])
                message = tc.input if not context else (
                    "Contexto previo de la conversacion de prueba:\n"
                    f"{context}\n\nMensaje actual del usuario:\n{tc.input}"
                )
                text, latency_ms = llamar_agente(self.agent_id, message, self.el_key)
                return RunnerResult(
                    output_text=text,
                    retrieval_context=["[ELEVENLABS] Conversacion real text-only ejecutada."],
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                return RunnerResult(output_text="", retrieval_context=[], latency_ms=0.0, error=str(exc))

        return runner

    def _build_direct_runner(self):
        """Runner de fallback usando OpenAI directamente con el system prompt."""
        sistema = self.analisis.get("prompt", {}).get("completo", "")
        modelo = self.analisis.get("metricas", {}).get("modelo_llm") or "gpt-4o"
        primer_msg = self.analisis.get("identidad", {}).get("primer_mensaje") or ""
        openai_key = self.openai_key

        def runner(tc: TestCase) -> RunnerResult:
            if not openai_key:
                return RunnerResult(output_text="", retrieval_context=[], latency_ms=0.0, error="OPENAI_API_KEY no configurada")
            try:
                import time
                from openai import OpenAI

                client = OpenAI(api_key=openai_key)
                mensajes = [{"role": "system", "content": sistema}]
                if primer_msg:
                    mensajes.append({"role": "assistant", "content": primer_msg})
                for ctx_line in (tc.context or []):
                    if ctx_line.startswith("user: "):
                        mensajes.append({"role": "user", "content": ctx_line[6:]})
                    elif ctx_line.startswith("agent: "):
                        mensajes.append({"role": "assistant", "content": ctx_line[7:]})
                mensajes.append({"role": "user", "content": tc.input})
                t0 = time.time()
                resp = client.chat.completions.create(
                    model=modelo,
                    messages=mensajes,
                    max_tokens=600,
                    temperature=0.3,
                )
                return RunnerResult(
                    output_text=resp.choices[0].message.content or "",
                    retrieval_context=[],
                    latency_ms=(time.time() - t0) * 1000,
                )
            except Exception as exc:
                return RunnerResult(output_text="", retrieval_context=[], latency_ms=0.0, error=str(exc))

        return runner

    def send_message(self, message: str, history: List[Dict[str, str]]) -> tuple[str, float]:
        """Envia un mensaje al agente con historial de conversacion."""
        if self.modo_ejecucion == "real":
            if not self.el_key:
                self.last_debug = {
                    "provider": "elevenlabs",
                    "agent_id": self.agent_id,
                    "mode": "real_session",
                    "error": "ELEVENLABS_API_KEY no configurada",
                }
                return "[ERROR: ELEVENLABS_API_KEY no configurada]", 0.0
            try:
                if self._real_session is None:
                    from juez.evaluation.adapters.elevenlabs_adapter import ElevenLabsConversationSession
                    self._real_session = ElevenLabsConversationSession(self.agent_id, self.el_key)
                text, latency_ms = self._real_session.send_message(message)
                self.last_debug = {
                    "provider": "elevenlabs",
                    "agent_id": self.agent_id,
                    "mode": "real_session",
                    "latency_ms": round(latency_ms, 1),
                    "message_preview": message[:240],
                    "response_preview": text[:500],
                    "turn": len(history) // 2 + 1,
                }
                return text, latency_ms
            except Exception as exc:
                self.last_debug = {
                    "provider": "elevenlabs",
                    "agent_id": self.agent_id,
                    "mode": "real_session",
                    "error": str(exc)[:300],
                    "turn": len(history) // 2 + 1,
                }
                return f"[ERROR: {exc}]", 0.0

        recent_history = history[-6:] if len(history) > 6 else history
        tc = TestCase(
            case_id=f"turn_{len(history) + 1}",
            input=message,
            context=[f"{h['role']}: {h['content']}" for h in recent_history],
        )
        result = self._runner(tc)
        self.last_debug = {
            "provider": "elevenlabs",
            "agent_id": self.agent_id,
            "mode": self.modo_ejecucion,
            "latency_ms": round(result.latency_ms or 0.0, 1),
            "message_preview": message[:240],
            "response_preview": (result.output_text or "")[:500],
            "error": result.error,
            "turn": len(history) // 2 + 1,
        }

        if result.error:
            return f"[ERROR: {result.error}]", result.latency_ms
        return result.output_text or "", result.latency_ms
