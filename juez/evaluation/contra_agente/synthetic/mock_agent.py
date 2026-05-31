"""MockAgent — mini-LLM con function calling que simula al agente bajo test.

Recibe el system prompt real del agente y sus tools (extraídos del análisis
estático del flow n8n). Por cada turno, decide qué decir y/o qué tool llamar.
Las tools no se ejecutan de verdad — el MockToolRunner les pasa respuestas
sintéticas.

Modelo por default: gpt-4o-mini (configurable vía settings.JUEZ_E2E_MODEL).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .cost_meter import CostMeter
from .mock_tools import MockToolRunner

log = logging.getLogger("juez.synthetic.mock_agent")

# Límite defensivo para evitar loops infinitos de tool_calls
_MAX_TOOL_CALL_ITERATIONS = 8


def _slug_tool_name(name: str) -> str:
    """OpenAI exige nombres de tools que matcheen ^[a-zA-Z0-9_-]{1,64}$.
    Los nombres de tools en n8n pueden tener espacios/acentos — los sanitizamos."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip())
    return slug[:64] or "unnamed_tool"


def _enriquecer_system_prompt(
    base_prompt: str,
    herramientas: List[Dict[str, Any]],
) -> str:
    """Enriquece el system prompt original con few-shot context derivado del
    análisis estático del agente bajo test.

    Mantiene el `base_prompt` íntegro y le anexa 3 secciones:
      1. "## Comportamiento esperado por las tools disponibles" — una línea por
         tool con su nombre y descripción (200 chars máx).
      2. "## Estilo de respuesta" — guía general de tono y disciplina.
      3. "## Ejemplos" — 2 ejemplos genéricos (no overfit al cliente) que
         ilustran el patrón usuario → llamada a tool.

    Si no hay tools, el bloque de comportamiento queda con un mensaje neutro
    para no romper la estructura.
    """
    base = (base_prompt or "").rstrip()
    lineas: List[str] = [base] if base else []

    # ── Sección 1: comportamiento esperado por tool ────────────────────────────
    lineas.append("")
    lineas.append("## Comportamiento esperado por las tools disponibles")
    if herramientas:
        for h in herramientas:
            nombre = (h.get("nombre") or h.get("name") or "").strip()
            if not nombre:
                continue
            desc_raw = (h.get("descripcion") or h.get("description") or "").strip()
            desc = desc_raw[:200] if desc_raw else "(sin descripción)"
            lineas.append(f"- {nombre}: {desc}")
    else:
        lineas.append("- (no hay tools registradas — responde en lenguaje natural)")

    # ── Sección 2: estilo de respuesta ─────────────────────────────────────────
    lineas.append("")
    lineas.append("## Estilo de respuesta")
    lineas.append("- Confirma cordialmente cada paso que ejecutes (qué tool llamaste y con qué datos clave).")
    lineas.append("- Si falta un dato necesario para llamar una tool, pídelo explícitamente antes de actuar.")
    lineas.append("- No inventes datos del usuario: si no te los dieron, pregúntalos.")

    # ── Sección 3: ejemplos genéricos (no overfit al dominio) ──────────────────
    lineas.append("")
    lineas.append("## Ejemplos")
    lineas.append(
        "Ejemplo 1 — el usuario inicia mencionando un identificador de contrato/sesión:\n"
        "  Usuario: 'INICIO 1234 INICIAL'\n"
        "  Acción esperada: llamar a la tool de registro/inicio con los datos provistos "
        "(contrato_id=1234, tipo=INICIAL) y confirmar cordialmente la operación."
    )
    lineas.append(
        "Ejemplo 2 — el usuario menciona un ambiente o entidad de su flujo:\n"
        "  Usuario: 'Vamos a la cocina'\n"
        "  Acción esperada: llamar a la tool correspondiente a ese ambiente/entidad y "
        "confirmar que se registró antes de pedir el siguiente dato."
    )

    return "\n".join(lineas)


def _build_tool_definitions(
    herramientas: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Convierte la lista `analisis["herramientas"]` al formato function-calling
    de OpenAI. Retorna `(defs, name_map)` donde `name_map[slug] = nombre_original`."""
    defs: List[Dict[str, Any]] = []
    name_map: Dict[str, str] = {}  # slug → nombre original (para devolverlo al runner)
    for h in herramientas:
        original = h.get("nombre", "") or h.get("name", "")
        if not original:
            continue
        slug = _slug_tool_name(original)
        name_map[slug] = original
        desc = (h.get("descripcion") or h.get("description") or original)[:600]
        # Schema permisivo: aceptamos un objeto libre. Si el agente real tiene
        # params específicos, el mock LLM los infiere del prompt + descripción.
        defs.append({
            "type": "function",
            "function": {
                "name": slug,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
        })
    return defs, name_map


class MockAgent:
    """Agente sintético — actúa como el agente bajo test usando function calling."""

    def __init__(
        self,
        system_prompt: str,
        herramientas: List[Dict[str, Any]],
        model: str,
        openai_key: str,
        temperature: float = 0.2,
        cost_meter: Optional[CostMeter] = None,
    ) -> None:
        from openai import OpenAI  # lazy import
        self._client = OpenAI(api_key=openai_key)
        self._model = model
        self._temperature = temperature
        self._cost_meter = cost_meter
        self._tool_defs, self._name_map = _build_tool_definitions(herramientas or [])
        enriched_prompt = _enriquecer_system_prompt(
            system_prompt or "Eres un asistente.",
            herramientas or [],
        )
        self._messages: List[Dict[str, Any]] = [
            {"role": "system", "content": enriched_prompt}
        ]

    @property
    def conversation(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    def respond(self, user_message: str, tool_runner: MockToolRunner) -> str:
        """Procesa un turno del usuario. Resuelve tool_calls en cascada hasta
        que el modelo emita un mensaje de texto puro (sin tool_calls).
        Retorna ese texto (lo que vería el usuario)."""
        self._messages.append({"role": "user", "content": user_message})

        for iteration in range(_MAX_TOOL_CALL_ITERATIONS):
            kwargs: Dict[str, Any] = {
                "model": self._model,
                "messages": self._messages,
                "temperature": self._temperature,
            }
            if self._tool_defs:
                kwargs["tools"] = self._tool_defs
                kwargs["tool_choice"] = "auto"

            response = self._client.chat.completions.create(**kwargs)

            # Tracking de tokens (best-effort: si la SDK no devolvió usage o
            # el meter no fue inyectado, simplemente no contamos).
            if self._cost_meter is not None:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    try:
                        self._cost_meter.track(
                            self._model,
                            getattr(usage, "prompt_tokens", 0) or 0,
                            getattr(usage, "completion_tokens", 0) or 0,
                        )
                    except Exception:  # pragma: no cover — defensivo
                        log.debug("cost_meter.track falló silenciosamente", exc_info=True)

            choice = response.choices[0]
            msg = choice.message

            # Persistir el mensaje del assistant en el historial
            assistant_entry: Dict[str, Any] = {"role": "assistant"}
            if msg.content is not None:
                assistant_entry["content"] = msg.content
            if msg.tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            self._messages.append(assistant_entry)

            # Si no llamó tools → devolvemos el texto y cortamos
            if not msg.tool_calls:
                return (msg.content or "").strip()

            # Ejecutar cada tool_call vía MockToolRunner y meter resultado al historial
            for tc in msg.tool_calls:
                slug = tc.function.name
                original_name = self._name_map.get(slug, slug)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = tool_runner.run(original_name, args)
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

        # Si saturó el loop, devolvemos lo que tengamos como último contenido textual
        log.warning("mock_agent saturó MAX_TOOL_CALL_ITERATIONS=%d", _MAX_TOOL_CALL_ITERATIONS)
        for m in reversed(self._messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return "[mock_agent saturó iteraciones]"
