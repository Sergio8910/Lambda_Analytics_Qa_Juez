"""Runner que ejecuta herramientas reales vía OpenAI Function Calling.

Flujo completo por caso:
  1. LLM del agente decide qué tool llamar (con los tools del agente como OpenAI functions)
  2. Se ejecuta el webhook HTTP real con los parámetros generados por el LLM
  3. El resultado del webhook se devuelve al LLM
  4. El LLM genera la respuesta final para el usuario
  5. Se retorna RunnerResult con output_text + envelope con detalles de ejecución

Para casos sin herramientas (happy_path, caos, etc.) funciona igual que LLM directo.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests as _req

from ..contracts import AgentEnvelope, RunnerResult, ToolCall


def _tools_to_openai(tools: List[Dict]) -> Tuple[List[Dict], Dict[str, Dict]]:
    """Convierte tools de ElevenLabs al formato OpenAI Function Calling.

    Retorna (openai_tools_list, config_por_nombre).
    Solo incluye tools de tipo webhook con URL válida.
    """
    openai_tools: List[Dict] = []
    configs: Dict[str, Dict] = {}

    for t in tools:
        if t.get("tipo", "").lower() != "webhook" or not t.get("url", "").strip():
            continue

        props: Dict[str, Any] = {}
        schema = t.get("param_schema") or {}
        campos_req: List[str] = t.get("campos_requeridos") or []
        campos_opt: List[str] = t.get("campos_opcionales") or []

        for campo in campos_req + campos_opt:
            val = schema.get(campo)
            if isinstance(val, dict):
                # Eliminar claves con valor None — OpenAI rechaza p.ej. "enum": null
                props[campo] = {k: v for k, v in val.items() if v is not None}
            else:
                props[campo] = {"type": "string", "description": campo}

        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["nombre"],
                "description": t.get("descripcion") or f"Herramienta {t['nombre']}",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": campos_req,
                },
            },
        })
        configs[t["nombre"]] = {
            "url": t["url"],
            "method": t.get("metodo", "POST").upper(),
        }

    return openai_tools, configs


def _ejecutar_webhook(
    tool_name: str,
    args: Dict[str, Any],
    config: Dict[str, Any],
    elevenlabs_key: str,
    timeout_s: float = 20.0,
) -> Dict[str, Any]:
    """Ejecuta el webhook HTTP real y retorna un dict con status + resultado."""
    url = config.get("url", "")
    method = config.get("method", "POST")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "xi-api-key": elevenlabs_key,
    }

    try:
        if method == "GET":
            resp = _req.get(url, params=args, headers=headers, timeout=timeout_s)
        else:
            resp = _req.request(method, url, json=args, headers=headers, timeout=timeout_s)

        try:
            body = resp.json()
        except Exception:
            body = {"raw_text": resp.text[:500]}

        return {
            "ok": resp.ok,
            "status_code": resp.status_code,
            "body": body,
            "error": None,
        }

    except _req.exceptions.Timeout:
        return {"ok": False, "status_code": None, "body": None,
                "error": f"Webhook timeout >{timeout_s}s"}
    except _req.exceptions.ConnectionError as e:
        return {"ok": False, "status_code": None, "body": None,
                "error": f"ConnectionError: {e}"}
    except Exception as e:
        return {"ok": False, "status_code": None, "body": None,
                "error": str(e)}


def crear_runner_con_tools_reales(
    analisis: Dict,
    agent_name: str,
    openai_key: str,
    elevenlabs_key: str = "",
) -> Any:
    """Crea un runner que usa OpenAI Function Calling + ejecución real de webhooks.

    Proceso autónomo por caso:
    - Si el LLM decide llamar una herramienta → el webhook se ejecuta de verdad.
    - Si el LLM no llama herramientas → responde directamente (igual que LLM directo).
    - El resultado del webhook y la respuesta final quedan en el RunnerResult.envelope.

    Thread-safe: no comparte estado mutable entre casos.
    """
    sistema = analisis["prompt"]["completo"]
    modelo = analisis["metricas"]["modelo_llm"] or "gpt-4o"
    primer_msg = analisis["identidad"]["primer_mensaje"] or ""

    openai_tools, tool_configs = _tools_to_openai(analisis.get("tools", []))
    tiene_tools = bool(openai_tools)

    def runner(tc) -> RunnerResult:
        if not openai_key:
            return RunnerResult(
                output_text="",
                retrieval_context=[],
                latency_ms=0.0,
                error="OPENAI_API_KEY no configurada",
            )

        try:
            from openai import OpenAI as _OAI
            client = _OAI(api_key=openai_key)

            mensajes: List[Dict] = [{"role": "system", "content": sistema}]
            if primer_msg:
                mensajes.append({"role": "assistant", "content": primer_msg})
            # Inyectar historial de turnos anteriores
            for ctx_line in (tc.context or []):
                if ctx_line.startswith("user: "):
                    mensajes.append({"role": "user", "content": ctx_line[6:]})
                elif ctx_line.startswith("agent: "):
                    mensajes.append({"role": "assistant", "content": ctx_line[7:]})
            mensajes.append({"role": "user", "content": tc.input})

            t0 = time.time()
            tool_executions: List[Dict] = []

            # ── Primera llamada al LLM (con tools si están disponibles) ────────
            call_kwargs: Dict[str, Any] = {
                "model": modelo,
                "messages": mensajes,
                "max_tokens": 600,
                "temperature": 0.3,
            }
            if tiene_tools:
                call_kwargs["tools"] = openai_tools
                call_kwargs["tool_choice"] = "auto"

            resp1 = client.chat.completions.create(**call_kwargs)
            msg1 = resp1.choices[0].message

            if msg1.tool_calls:
                # ── El LLM decidió llamar una o más herramientas ────────────
                mensajes.append({
                    "role": "assistant",
                    "content": msg1.content or "",
                    "tool_calls": [
                        {
                            "id": tc_call.id,
                            "type": "function",
                            "function": {
                                "name": tc_call.function.name,
                                "arguments": tc_call.function.arguments,
                            },
                        }
                        for tc_call in msg1.tool_calls
                    ],
                })

                for tc_call in msg1.tool_calls:
                    tool_name = tc_call.function.name
                    try:
                        args = json.loads(tc_call.function.arguments or "{}")
                    except Exception:
                        args = {}

                    cfg = tool_configs.get(tool_name)
                    if cfg:
                        resultado_webhook = _ejecutar_webhook(
                            tool_name, args, cfg, elevenlabs_key
                        )
                    else:
                        resultado_webhook = {
                            "ok": False,
                            "status_code": None,
                            "body": None,
                            "error": f"Tool '{tool_name}' sin configuración de URL (¿es system tool?)",
                        }

                    tool_executions.append({
                        "tool": tool_name,
                        "args": args,
                        "result": resultado_webhook,
                    })

                    mensajes.append({
                        "role": "tool",
                        "tool_call_id": tc_call.id,
                        "content": json.dumps(resultado_webhook, ensure_ascii=False),
                    })

                # ── Segunda llamada: respuesta final con el resultado ────────
                resp2 = client.chat.completions.create(
                    model=modelo,
                    messages=mensajes,
                    max_tokens=600,
                    temperature=0.3,
                )
                respuesta_final = resp2.choices[0].message.content or ""
            else:
                respuesta_final = msg1.content or ""

            latency_ms = (time.time() - t0) * 1000

            # ── Construir contexto de ejecución para métricas ────────────────
            retrieval_context: List[str] = []
            for te in tool_executions:
                r = te["result"]
                if r["ok"]:
                    estado = f"EXITO HTTP {r['status_code']}"
                    cuerpo = json.dumps(r.get("body") or {}, ensure_ascii=False)[:300]
                    retrieval_context.append(
                        f"[TOOL {te['tool']}] {estado} | args={te['args']} | respuesta: {cuerpo}"
                    )
                else:
                    estado = f"FALLO HTTP {r.get('status_code', 'N/A')}: {r.get('error', 'desconocido')}"
                    retrieval_context.append(
                        f"[TOOL {te['tool']}] {estado} | args={te['args']}"
                    )

            envelope = AgentEnvelope(
                output_text=respuesta_final,
                retrieval_context=retrieval_context,
                tool_calls=[
                    ToolCall(name=te["tool"], arguments=te["args"])
                    for te in tool_executions
                ],
                raw={"tool_executions": tool_executions},
                latency_ms=latency_ms,
            )

            return RunnerResult(
                output_text=respuesta_final,
                retrieval_context=retrieval_context,
                latency_ms=latency_ms,
                error=None,
                envelope=envelope,
            )

        except Exception as exc:
            return RunnerResult(
                output_text="",
                retrieval_context=[],
                latency_ms=0.0,
                error=str(exc),
            )

    return runner
