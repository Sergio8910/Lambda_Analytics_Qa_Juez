"""Ingesta de conversaciones reales de produccion desde ElevenLabs.

Flujo:
  1. Fetches las N conversaciones mas recientes del agente via ElevenLabs API.
  2. GPT analiza los patrones de fallo reales.
  3. Genera planes de conversacion (ConversationPlan) que atacan exactamente
     esos patrones — casos que ya fallaron en produccion, no escenarios hipoteticos.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.parse


_ELEVENLABS_API = "https://api.elevenlabs.io/v1"

_SYSTEM_ANALIZADOR = (
    "Eres un experto en evaluación de agentes de voz conversacionales. "
    "Recibes transcripciones de conversaciones reales y debes identificar "
    "patrones de fallo y oportunidades de mejora. Responde en español."
)

_SYSTEM_GENERADOR = (
    "Eres un experto en evaluación de agentes de voz. Generas planes de conversación "
    "de prueba basados en fallos reales observados en producción. "
    "REGLA ABSOLUTA: Nunca uses placeholders como [valor], [nombre], [dato] — "
    "siempre datos colombianos inventados pero concretos. "
    "Responde ÚNICAMENTE con JSON válido."
)


# ---------------------------------------------------------------------------
# ElevenLabs API — fetch conversations
# ---------------------------------------------------------------------------

def _el_get(path: str, el_key: str) -> Dict:
    url = f"{_ELEVENLABS_API}{path}"
    req = urllib.request.Request(
        url,
        headers={"xi-api-key": el_key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def _listar_conversaciones(agent_id: str, el_key: str, n: int) -> List[Dict]:
    """Retorna los N ids + metadatos de conversaciones más recientes."""
    params = urllib.parse.urlencode({
        "agent_id": agent_id,
        "page_size": min(n, 100),
    })
    data = _el_get(f"/convai/conversations?{params}", el_key)
    conversaciones = data.get("conversations", [])
    return conversaciones[:n]


def _obtener_transcripcion(conv_id: str, el_key: str) -> List[Dict[str, str]]:
    """Retorna la transcripción como lista de {role, message}."""
    data = _el_get(f"/convai/conversations/{conv_id}", el_key)
    transcript = data.get("transcript", [])
    return [
        {"role": t.get("role", "unknown"), "message": t.get("message", "")}
        for t in transcript
        if t.get("message", "").strip()
    ]


# ---------------------------------------------------------------------------
# GPT — analizar patrones y generar planes
# ---------------------------------------------------------------------------

def _analizar_patrones(
    transcripciones: List[Dict],
    agent_name: str,
    analisis: Dict,
    openai_key: str,
    modelo: str = "gpt-4o",
) -> str:
    """Pide a GPT que identifique los patrones de fallo en las conversaciones reales."""
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)

    # Preparar resumen de transcripciones
    bloques = []
    for i, conv in enumerate(transcripciones[:15], start=1):
        turns = conv.get("transcript", [])
        if not turns:
            continue
        lineas = [f"[{t['role'].upper()}] {t['message'][:200]}" for t in turns[:8]]
        bloques.append(f"--- Conversación {i} ---\n" + "\n".join(lineas))

    if not bloques:
        return "Sin transcripciones disponibles para analizar."

    contexto_agente = analisis.get("prompt", {}).get("completo", "")[:1000]
    texto_convs = "\n\n".join(bloques)

    resp = client.chat.completions.create(
        model=modelo,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM_ANALIZADOR},
            {"role": "user", "content": (
                f"Agente: {agent_name}\n"
                f"Contexto del agente: {contexto_agente}\n\n"
                f"Transcripciones reales de producción:\n\n{texto_convs}\n\n"
                "Identifica:\n"
                "1. PATRONES DE FALLO: situaciones donde el agente respondió mal, "
                "se confundió, no invocó tools, perdió el hilo o respondió fuera de dominio.\n"
                "2. CASOS LÍMITE NO CUBIERTOS: preguntas o situaciones que el agente no manejó bien.\n"
                "3. TOP 5 ESCENARIOS A PROBAR: los escenarios más críticos a probar basados en lo observado. "
                "Para cada uno: describe el escenario, el comportamiento esperado y por qué falló antes.\n"
                "Sé específico con los datos (nombres, productos, situaciones reales del dominio)."
            )},
        ],
        max_tokens=2000,
    )
    return resp.choices[0].message.content or ""


def _generar_planes_desde_patrones(
    analisis_patrones: str,
    agent_name: str,
    analisis: Dict,
    n_planes: int,
    openai_key: str,
    modelo: str = "gpt-4o",
) -> List[Dict]:
    """Genera planes de conversación atacando los patrones reales identificados."""
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)

    tools_txt = ""
    for t in analisis.get("tools", []):
        if t.get("tipo", "").lower() == "webhook":
            campos = ", ".join(t.get("campos_requeridos", []))
            tools_txt += f"- {t['nombre']}: {t.get('descripcion', '')} (campos: {campos})\n"

    resp = client.chat.completions.create(
        model=modelo,
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_GENERADOR},
            {"role": "user", "content": (
                f"Agente: {agent_name}\n"
                f"Tools disponibles:\n{tools_txt or 'Ninguna'}\n\n"
                f"Patrones de fallo identificados en producción:\n{analisis_patrones}\n\n"
                f"Genera exactamente {n_planes} planes de conversación que ataquen directamente "
                f"estos patrones reales. Cada plan debe:\n"
                "- Reproducir una situación que ya causó problemas en producción\n"
                "- Usar datos colombianos concretos (nombres, ciudades, referencias reales)\n"
                "- Tener success_criteria claro y verificable\n"
                "- Ser más específico que un test genérico — debe replicar el fallo real\n\n"
                "Usa este JSON exacto:\n"
                '{"plans": [{'
                '"plan_id": "real_01", '
                '"category": "happy_path|herramienta|multi_turno|limite|caos|agresivo|seguridad|contexto_multiple", '
                '"severity": "alta|media", '
                '"tags": ["produccion", "patron_real"], '
                '"success_threshold": 0.70, '
                '"max_turns": 3, '
                '"persona": {"name": "...", "mood": "...", "backstory": "...", "language_style": "informal"}, '
                '"turns": [{'
                '"turn_id": 1, "turn_type": "opener", "intent": "...", '
                '"message_template": "...", "success_criteria": "...", '
                '"metrics": ["task_success"], "adaptive_logic": null, "variables": {}'
                '}], "notes": "Basado en patron real de produccion"'
                '}]}'
            )},
        ],
        max_tokens=3000,
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
        return data.get("plans", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Conversión a ConversationPlan
# ---------------------------------------------------------------------------

def _dict_a_plan(p: Dict, batch_id: str, agent_id: str, adapter: str, idx: int):
    """Convierte un dict de plan a ConversationPlan. Reutiliza _parse_plans lógica."""
    try:
        from evaluation.contra_agente.models import (
            AdaptiveLogic, ConversationPlan, Persona, TurnSpec,
        )
        persona_data = p.get("persona", {})
        persona = Persona(
            name=persona_data.get("name", f"Usuario{idx}"),
            mood=persona_data.get("mood", "cordial"),
            backstory=persona_data.get("backstory", "Usuario real de producción"),
            language_style=persona_data.get("language_style", "informal"),
        )
        turns = []
        for t in p.get("turns", []):
            adaptive_raw = t.get("adaptive_logic")
            adaptive = None
            if adaptive_raw and isinstance(adaptive_raw, dict):
                conditions = adaptive_raw.get("conditions", [])
                if conditions:
                    adaptive = AdaptiveLogic(conditions=conditions)
            turns.append(TurnSpec(
                turn_id=int(t.get("turn_id", len(turns) + 1)),
                turn_type=t.get("turn_type", "probe"),
                intent=t.get("intent", ""),
                message_template=t.get("message_template", ""),
                success_criteria=t.get("success_criteria", ""),
                metrics=t.get("metrics", ["task_success"]),
                adaptive_logic=adaptive,
                variables=t.get("variables", {}),
            ))
        if not turns:
            return None
        return ConversationPlan(
            plan_id=p.get("plan_id", f"real_{idx:02d}"),
            category=p.get("category", "happy_path"),
            severity=p.get("severity", "media"),
            tags=p.get("tags", ["produccion", "patron_real"]),
            success_threshold=float(p.get("success_threshold", 0.70)),
            max_turns=len(turns),
            persona=persona,
            turns=turns,
            notes=p.get("notes"),
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def ingestar_conversaciones_reales(
    agent_id: str,
    el_key: str,
    n: int,
    analisis: Dict[str, Any],
    openai_key: str,
    agent_name: str = "",
    n_planes: int = 5,
    verbose: bool = True,
) -> List[Any]:
    """
    Descarga las N conversaciones más recientes, identifica patrones de fallo
    con GPT y genera planes de prueba dirigidos. Retorna lista de ConversationPlan.
    """
    if not el_key or not openai_key:
        if verbose:
            print("  [ingester] Faltan credenciales — omitiendo ingesta de conversaciones reales.")
        return []

    if verbose:
        print(f"  [ingester] Descargando {n} conversaciones reales de producción...")

    # 1. Listar conversaciones
    conv_metas = _listar_conversaciones(agent_id, el_key, n)
    if not conv_metas:
        if verbose:
            print("  [ingester] No se encontraron conversaciones para este agente.")
        return []

    # 2. Obtener transcripciones
    convs_con_transcript = []
    for meta in conv_metas:
        conv_id = meta.get("conversation_id") or meta.get("id", "")
        if not conv_id:
            continue
        transcript = _obtener_transcripcion(conv_id, el_key)
        if transcript:
            convs_con_transcript.append({
                "id": conv_id,
                "transcript": transcript,
                "metadata": meta,
            })

    if not convs_con_transcript:
        if verbose:
            print("  [ingester] Las conversaciones descargadas no tienen transcripciones.")
        return []

    if verbose:
        print(f"  [ingester] {len(convs_con_transcript)} conversaciones con transcripción. Analizando patrones...")

    # 3. Analizar patrones con GPT
    patrones = _analizar_patrones(
        convs_con_transcript, agent_name or agent_id, analisis, openai_key
    )

    if verbose:
        print(f"  [ingester] Generando {n_planes} planes dirigidos a patrones reales...")

    # 4. Generar planes
    planes_dict = _generar_planes_desde_patrones(
        patrones, agent_name or agent_id, analisis, n_planes, openai_key
    )

    # 5. Convertir a ConversationPlan
    batch_id = f"batch_real_{uuid.uuid4().hex[:6]}"
    planes = []
    for i, p_dict in enumerate(planes_dict, start=1):
        plan = _dict_a_plan(p_dict, batch_id, agent_id, "elevenlabs", i)
        if plan:
            planes.append(plan)

    if verbose:
        print(f"  [ingester] {len(planes)} planes reales generados.")

    return planes
