"""Runner de voz real: TTS → ElevenLabs ConvAI WebSocket (audio) → Whisper STT.

Pipeline completo por caso:
  1. ElevenLabs TTS convierte el texto de prueba a audio PCM 16kHz
  2. El audio se envía por el WebSocket de ConvAI (modo audio, no text_only)
  3. Se recibe la respuesta de audio del agente
  4. OpenAI Whisper transcribe el audio recibido
  5. Se retorna el texto transcrito + métricas de latencia real

Esto prueba la capa que el runner LLM directo no puede probar:
  - ASR (¿el agente entiende lo que dijo el usuario?)
  - TTS (¿la respuesta suena natural y coherente?)
  - Latencia real de audio end-to-end
  - Manejo de turnos en modo voz
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests as _req

from ..contracts import AgentEnvelope, RunnerResult


ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
ELEVENLABS_WS = "wss://api.elevenlabs.io/v1/convai/conversation"


def _sintetizar_audio(
    texto: str,
    voice_id: str,
    api_key: str,
    modelo_tts: str = "eleven_flash_v2_5",
) -> bytes:
    """Llama a ElevenLabs TTS y retorna bytes PCM 16kHz 16-bit mono.

    Si falla, lanza excepción que el runner captura.
    """
    resp = _req.post(
        f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}/stream",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm;rate=16000",
        },
        json={
            "text": texto,
            "model_id": modelo_tts,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
            "output_format": "pcm_16000",
        },
        stream=True,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"ElevenLabs TTS error {resp.status_code}: {resp.text[:200]}")

    return b"".join(resp.iter_content(chunk_size=4096))


def _transcribir_audio(audio_bytes: bytes, openai_key: str) -> str:
    """Transcribe bytes de audio (PCM/WAV) con OpenAI Whisper."""
    from openai import OpenAI

    # Whisper espera un archivo con extensión reconocible; usamos WAV
    # Encapsulamos el PCM crudo en un contenedor WAV mínimo
    wav_bytes = _pcm_to_wav(audio_bytes, sample_rate=16000, channels=1, bits=16)

    client = OpenAI(api_key=openai_key)
    wav_file = io.BytesIO(wav_bytes)
    wav_file.name = "audio.wav"

    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=wav_file,
        language="es",
    )
    return transcript.text or ""


def _pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
) -> bytes:
    """Envuelve PCM crudo en un header WAV válido."""
    import struct

    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,       # chunk size
        1,        # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm_data


def _parse_ws(raw) -> Optional[Dict]:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _conversar_voz_async(
    audio_input: bytes,
    agent_id: str,
    api_key: str,
    timeout: float = 40.0,
) -> Tuple[bytes, float]:
    """Envía audio al ConvAI WebSocket y recibe el audio de respuesta.

    Retorna (audio_bytes_respuesta, latency_ms).
    audio_bytes_respuesta es PCM crudo (concatenación de chunks base64).
    """
    try:
        import websockets
    except ImportError:
        raise ImportError("Instala websockets: pip install 'websockets>=11'")

    uri = f"{ELEVENLABS_WS}?agent_id={agent_id}&xi-api-key={api_key}"
    t0 = time.time()
    audio_chunks: List[bytes] = []
    loop = asyncio.get_event_loop()

    async with websockets.connect(uri, ping_interval=None, open_timeout=15) as ws:
        # 1. Esperar conversation_initiation_metadata
        dl = loop.time() + 10
        while loop.time() < dl:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = _parse_ws(raw)
                if msg and msg.get("type") == "conversation_initiation_metadata":
                    break
            except asyncio.TimeoutError:
                break
            except Exception:
                continue

        # 2. Configuración: habilitar audio (no text_only) y silenciar first_message
        await ws.send(json.dumps({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "conversation": {"text_only": False},
                "agent": {"first_message": ""},
            },
        }))

        # 3. Enviar audio de entrada en chunks de 1024 bytes
        chunk_size = 1024
        for i in range(0, len(audio_input), chunk_size):
            chunk = audio_input[i:i + chunk_size]
            await ws.send(json.dumps({
                "user_audio_chunk": base64.b64encode(chunk).decode("utf-8"),
            }))

        # 4. Escuchar respuesta hasta que termine o se agote el tiempo
        dl = loop.time() + timeout
        agent_responded = False

        while loop.time() < dl:
            try:
                remaining = max(1.0, dl - loop.time())
                raw = await asyncio.wait_for(ws.recv(), timeout=min(15.0, remaining))
                msg = _parse_ws(raw)
                if msg is None:
                    continue

                tipo = msg.get("type", "")

                if tipo == "audio":
                    # Audio del agente en base64
                    audio_b64 = msg.get("audio_event", {}).get("audio_base_64", "") or ""
                    if audio_b64:
                        audio_chunks.append(base64.b64decode(audio_b64))
                        agent_responded = True

                elif tipo == "agent_response":
                    # Señal de que la respuesta terminó
                    if agent_responded:
                        break

                elif tipo == "ping":
                    await ws.send(json.dumps({
                        "type": "pong",
                        "event_id": msg.get("ping_event", {}).get("event_id", 0),
                    }))

                elif tipo in ("conversation_ended", "error"):
                    break

            except asyncio.TimeoutError:
                if agent_responded:
                    break
            except Exception:
                continue

    audio_response = b"".join(audio_chunks)
    latency_ms = (time.time() - t0) * 1000
    return audio_response, latency_ms


def crear_runner_voz_real(
    analisis: Dict,
    agent_name: str,
    agent_id: str,
    elevenlabs_key: str,
    openai_key: str,
) -> Any:
    """Crea un runner de voz real: TTS → ConvAI → Whisper STT.

    Completamente autónomo: genera audio, lo envía, recibe y transcribe.
    Thread-safe: cada llamada crea su propio event loop.

    Use este runner en lugar del LLM directo para probar la capa de voz real.
    """
    voice_id = analisis["voz"]["voice_id"]
    modelo_tts = analisis["voz"]["modelo_tts"] or "eleven_flash_v2_5"

    def runner(tc) -> RunnerResult:
        if not elevenlabs_key:
            return RunnerResult(
                output_text="",
                retrieval_context=[],
                latency_ms=0.0,
                error="ELEVENLABS_API_KEY no configurada",
            )
        if not openai_key:
            return RunnerResult(
                output_text="",
                retrieval_context=[],
                latency_ms=0.0,
                error="OPENAI_API_KEY requerida para transcripción Whisper",
            )

        try:
            t_total = time.time()

            # ── Paso 1: TTS — convertir input a audio ────────────────────────
            t_tts = time.time()
            audio_input = _sintetizar_audio(tc.input, voice_id, elevenlabs_key, modelo_tts)
            latency_tts = (time.time() - t_tts) * 1000

            # ── Paso 2: ConvAI — enviar audio y recibir respuesta ────────────
            loop = asyncio.new_event_loop()
            try:
                audio_response, latency_ws = loop.run_until_complete(
                    _conversar_voz_async(audio_input, agent_id, elevenlabs_key)
                )
            finally:
                loop.close()

            if not audio_response:
                return RunnerResult(
                    output_text="",
                    retrieval_context=[],
                    latency_ms=(time.time() - t_total) * 1000,
                    error="ConvAI no devolvió audio (timeout o sin respuesta)",
                )

            # ── Paso 3: STT — transcribir audio de respuesta con Whisper ────
            t_stt = time.time()
            texto_transcrito = _transcribir_audio(audio_response, openai_key)
            latency_stt = (time.time() - t_stt) * 1000

            latency_total = (time.time() - t_total) * 1000

            envelope = AgentEnvelope(
                output_text=texto_transcrito,
                retrieval_context=[],
                raw={
                    "pipeline": "voz_real",
                    "latency_tts_ms": round(latency_tts),
                    "latency_ws_ms": round(latency_ws),
                    "latency_stt_ms": round(latency_stt),
                    "audio_response_bytes": len(audio_response),
                    "transcripcion": texto_transcrito,
                },
                latency_ms=latency_total,
            )

            return RunnerResult(
                output_text=texto_transcrito,
                retrieval_context=[],
                latency_ms=latency_total,
                error=None,
                envelope=envelope,
            )

        except Exception as exc:
            return RunnerResult(
                output_text="",
                retrieval_context=[],
                latency_ms=0.0,
                error=f"Voice runner error: {exc}",
            )

    return runner
