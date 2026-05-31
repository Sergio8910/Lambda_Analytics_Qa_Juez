"""Adaptador WebSocket para ElevenLabs Conversational AI.

Protocolo text_only real (verificado via debug):
  Server → agent_chat_response_part (start/delta/stop) + agent_response
  Client → user_message (después de recibir el saludo del agente)
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Tuple


ELEVENLABS_WS = "wss://api.elevenlabs.io/v1/convai/conversation"


def _parse(raw) -> dict | None:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _esperar_agent_response(ws, timeout: float) -> str:
    """Lee mensajes hasta recibir agent_response completo. Retorna el texto."""
    loop = asyncio.get_event_loop()
    dl = loop.time() + timeout
    texto = ""

    while loop.time() < dl:
        try:
            remaining = max(1.0, dl - loop.time())
            raw = await asyncio.wait_for(ws.recv(), timeout=min(15.0, remaining))
            msg = _parse(raw)
            if msg is None:
                continue

            tipo = msg.get("type", "")

            if tipo == "agent_chat_response_part":
                parte = msg.get("text_response_part", {})
                if parte.get("type") == "delta":
                    texto += parte.get("text", "")

            elif tipo == "agent_response":
                evt = msg.get("agent_response_event", {})
                completo = evt.get("agent_response", "") or msg.get("agent_response", "")
                if completo:
                    texto = completo
                break  # respuesta completa recibida

            elif tipo == "ping":
                await ws.send(json.dumps({
                    "type": "pong",
                    "event_id": msg.get("ping_event", {}).get("event_id", 0),
                }))

            elif tipo == "client_tool_call":
                # ElevenLabs envía client_tool_call para tools del lado cliente
                # (ej. voicemail_detection). Si no respondemos, la conversación
                # queda bloqueada esperando el resultado.
                call_data = msg.get("client_tool_call", {})
                tool_name = call_data.get("tool_name", "")
                tool_call_id = call_data.get("tool_call_id", "")
                if "voicemail" in tool_name.lower():
                    result = json.dumps({"is_voicemail": False})
                else:
                    result = json.dumps({"status": "completed"})
                await ws.send(json.dumps({
                    "type": "client_tool_result",
                    "tool_call_id": tool_call_id,
                    "result": result,
                    "is_error": False,
                }))

            elif tipo in ("conversation_ended", "error"):
                break

        except asyncio.TimeoutError:
            if texto:
                break
        except Exception:
            continue

    return texto


async def _conversar_async(
    agent_id: str,
    mensaje: str,
    api_key: str,
    timeout: float = 20.0,
) -> Tuple[str, float]:
    try:
        import websockets
    except ImportError:
        raise ImportError("Instala websockets: pip install 'websockets>=11'")

    uri = f"{ELEVENLABS_WS}?agent_id={agent_id}&xi-api-key={api_key}"
    t0 = time.time()

    async with websockets.connect(uri, ping_interval=None, open_timeout=15) as ws:

        # 1. Esperar metadata de iniciación
        loop = asyncio.get_event_loop()
        dl = loop.time() + 10
        while loop.time() < dl:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = _parse(raw)
                if msg and msg.get("type") == "conversation_initiation_metadata":
                    break
            except asyncio.TimeoutError:
                break
            except Exception:
                continue

        # 2. Activar text_only y suprimir el first_message del agente.
        #    Sin esta supresión, el servidor usa el primer user_message para
        #    devolver el saludo preconfigrado y NO procesa un segundo mensaje,
        #    lo que haría que el mensaje de prueba nunca recibiera respuesta.
        await ws.send(json.dumps({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "conversation": {"text_only": True},
                "agent": {"first_message": ""},
            },
        }))

        # 3. Enviar el mensaje de prueba directamente como primer (y único) turno
        await ws.send(json.dumps({
            "type": "user_message",
            "user_message": mensaje,
        }))

        # 4. Recolectar la respuesta del agente
        respuesta = await _esperar_agent_response(ws, timeout=timeout)

    return respuesta, (time.time() - t0) * 1000


def llamar_agente(
    agent_id: str,
    mensaje: str,
    api_key: str,
    timeout: float = 20.0,
) -> Tuple[str, float]:
    """Llama al agente ElevenLabs vía WebSocket text_only.

    Thread-safe: crea su propio event loop por llamada (compatible con
    ThreadPoolExecutor del EvaluationEngine).
    Retorna (respuesta_texto, latency_ms).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _conversar_async(agent_id, mensaje, api_key, timeout)
        )
    except Exception as exc:
        return f"[ERROR: {exc}]", 0.0
    finally:
        loop.close()
