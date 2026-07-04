"""Adapter ElevenLabs para el contra-agente.

Reutiliza crear_runner_con_tools_reales de tools_runner.py.
Convierte el historial de conversación en contexto para el runner existente.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

# Asegurar que el path del proyecto esté disponible
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from juez.evaluation.contracts import RunnerResult
from juez.evaluation.report_models import TestCase


class ElevenLabsAdapter:
    """Adapter para agentes ElevenLabs ConvAI.

    Reutiliza la lógica de tools_runner para ejecutar mensajes con historial completo.
    """

    def __init__(
        self,
        agent_id: str,
        analisis: Dict[str, Any],
        openai_key: str,
        el_key: str = "",
    ) -> None:
        self.agent_id = agent_id
        self.analisis = analisis
        self.openai_key = openai_key
        self.el_key = el_key
        self._runner = self._build_runner()

    def _build_runner(self):
        try:
            from juez.evaluation.adapters.tools_runner import crear_runner_con_tools_reales
            return crear_runner_con_tools_reales(
                self.analisis, self.agent_id, self.openai_key, self.el_key
            )
        except Exception:
            # Fallback: LLM directo sin tools
            return self._build_direct_runner()

    def _build_direct_runner(self):
        """Runner de fallback usando OpenAI directamente con el system prompt."""
        sistema = self.analisis.get("prompt", {}).get("completo", "")
        modelo = self.analisis.get("metricas", {}).get("modelo_llm") or "gpt-4o"
        primer_msg = self.analisis.get("identidad", {}).get("primer_mensaje") or ""
        openai_key = self.openai_key

        def runner(tc: TestCase) -> RunnerResult:
            if not openai_key:
                return RunnerResult(
                    output_text="",
                    retrieval_context=[],
                    latency_ms=0.0,
                    error="OPENAI_API_KEY no configurada",
                )
            try:
                import time
                from openai import OpenAI
                client = OpenAI(api_key=openai_key)
                mensajes = [{"role": "system", "content": sistema}]
                if primer_msg:
                    mensajes.append({"role": "assistant", "content": primer_msg})
                # Inyectar historial desde context
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
                return RunnerResult(
                    output_text="",
                    retrieval_context=[],
                    latency_ms=0.0,
                    error=str(exc),
                )

        return runner

    def send_message(self, message: str, history: List[Dict[str, str]]) -> tuple[str, float]:
        """Envía un mensaje al agente con historial de conversación.

        Retorna (agent_response, latency_ms).
        """
        # Últimos 6 turnos como sliding window
        recent_history = history[-6:] if len(history) > 6 else history

        tc = TestCase(
            case_id=f"turn_{len(history) + 1}",
            input=message,
            context=[f"{h['role']}: {h['content']}" for h in recent_history],
        )
        result = self._runner(tc)

        if result.error:
            return f"[ERROR: {result.error}]", result.latency_ms

        return result.output_text or "", result.latency_ms
