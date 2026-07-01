"""Evaluación de CONVERSACIONES (transcripts ya ocurridos).

Recibe una conversación en JSON (agente + usuario, turnos y metadata) y evalúa
de forma INDEPENDIENTE el desempeño del agente contra su rol y las conductas
esperadas — NO confía en el `prompt_adherence` auto-reportado, lo verifica.

Dos capas:
  1. Chequeos determinísticos sobre el transcript (emojis, idioma, preguntas de
     aclaración, tiempos de respuesta, balance de turnos). Sin LLM, rápidos.
  2. LLM-as-judge opcional: puntúa dimensiones (adherencia al rol, utilidad,
     calidez, manejo de alcance, claridad) leyendo el transcript + el rol.

Aditivo: vive en el servicio prompt_eval, reusa su settings/OpenAI.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .settings import settings

log = logging.getLogger("prompt_eval.conversation")

# Rango amplio de emojis (suficiente para detección de uso).
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF✀-➿☀-⛿]"
)
_VEREDICTOS = (("excelente", 90), ("bueno", 75), ("aceptable", 60), ("deficiente", 40), ("critico", 0))


# =============================================================================
# Modelos
# =============================================================================


class Turn(BaseModel):
    turn_id: Optional[int] = None
    timestamp: Optional[str] = None
    speaker: str
    message: str = ""
    model_config = {"extra": "allow"}


class ConversationInput(BaseModel):
    conversation_id: str = Field("(sin id)")
    platform: Optional[str] = None
    agent_name: Optional[str] = "(agente)"
    agent_role: Optional[str] = ""
    language: Optional[str] = None
    turns: List[Turn] = Field(default_factory=list)
    conversation_metadata: Dict[str, Any] = Field(default_factory=dict)
    # Opcional: si lo tienen, el system prompt real del agente (evaluación más precisa).
    system_prompt: Optional[str] = None
    model_config = {"extra": "allow"}


class QAAgentRequest(BaseModel):
    agent_name: str = Field("(agente)")
    conversations: List[ConversationInput] = Field(default_factory=list)
    incluir_llm: bool = True
    model_config = {"extra": "allow"}


class CriterioResult(BaseModel):
    nombre: str
    cumple: Optional[bool] = None  # None = no determinable sin LLM
    score: float
    detalle: str


class ConversationFinding(BaseModel):
    severidad: str  # ALTO | MEDIO | BAJO | INFO
    mensaje: str
    turno: Optional[int] = None


class ConversationEvalResult(BaseModel):
    conversation_id: str
    agent_name: str
    agent_role: str
    score_global: float
    veredicto: str
    criterios: List[CriterioResult] = Field(default_factory=list)
    dimensiones_llm: Dict[str, Any] = Field(default_factory=dict)
    findings: List[ConversationFinding] = Field(default_factory=list)
    metricas: Dict[str, Any] = Field(default_factory=dict)
    recomendaciones: List[str] = Field(default_factory=list)
    llm_judge_aplicado: bool = False
    nota_metodo: str = ""
    model_config = {"extra": "forbid"}


# =============================================================================
# Capa 1 — chequeos determinísticos
# =============================================================================


def _agent_turns(conv: ConversationInput) -> List[Turn]:
    return [t for t in conv.turns if (t.speaker or "").lower() in ("agent", "assistant", "bot")]


def _user_turns(conv: ConversationInput) -> List[Turn]:
    return [t for t in conv.turns if (t.speaker or "").lower() in ("user", "customer", "human")]


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _avg_response_time(conv: ConversationInput) -> Optional[float]:
    """Tiempo promedio (s) que tarda el agente en responder tras el usuario."""
    deltas: List[float] = []
    for i in range(1, len(conv.turns)):
        prev, cur = conv.turns[i - 1], conv.turns[i]
        if (prev.speaker or "").lower() in ("user", "customer", "human") and \
           (cur.speaker or "").lower() in ("agent", "assistant", "bot"):
            a, b = _parse_ts(prev.timestamp), _parse_ts(cur.timestamp)
            if a and b:
                deltas.append((b - a).total_seconds())
    return round(sum(deltas) / len(deltas), 1) if deltas else None


def deterministic_checks(conv: ConversationInput) -> Dict[str, Any]:
    agent = _agent_turns(conv)
    user = _user_turns(conv)
    agent_text = "\n".join(t.message for t in agent)

    n_emoji = len(_EMOJI_RE.findall(agent_text))
    turnos_con_emoji = sum(1 for t in agent if _EMOJI_RE.search(t.message))
    turnos_con_pregunta = sum(1 for t in agent if "?" in t.message)

    # idioma esperado (de language o metadata); chequeo heurístico simple
    idioma_esperado = (conv.language or "").split("-")[0].lower() or None

    return {
        "total_turnos": len(conv.turns),
        "turnos_agente": len(agent),
        "turnos_usuario": len(user),
        "emojis_totales": n_emoji,
        "turnos_agente_con_emoji": turnos_con_emoji,
        "turnos_agente_con_pregunta": turnos_con_pregunta,
        "tiempo_respuesta_promedio_s": _avg_response_time(conv),
        "idioma_esperado": idioma_esperado,
        "longitud_promedio_respuesta": (
            round(sum(len(t.message) for t in agent) / len(agent)) if agent else 0
        ),
    }


def _criterios_deterministicos(conv: ConversationInput, met: Dict[str, Any]) -> List[CriterioResult]:
    """Verifica de forma objetiva los criterios que SÍ se pueden medir sin LLM."""
    agent = _agent_turns(conv)
    out: List[CriterioResult] = []

    # Uso de emojis
    usa_emoji = met["turnos_agente_con_emoji"] > 0
    out.append(CriterioResult(
        nombre="uso_de_emojis", cumple=usa_emoji,
        score=100.0 if usa_emoji else 0.0,
        detalle=f"{met['turnos_agente_con_emoji']}/{met['turnos_agente']} turnos del agente con emojis",
    ))

    # Preguntas de aclaración
    pregunta = met["turnos_agente_con_pregunta"] > 0
    out.append(CriterioResult(
        nombre="preguntas_de_aclaracion", cumple=pregunta,
        score=100.0 if pregunta else 40.0,
        detalle=f"{met['turnos_agente_con_pregunta']} turno(s) del agente con preguntas",
    ))

    return out


# =============================================================================
# Capa 2 — LLM-as-judge (opcional)
# =============================================================================

_LLM_SYSTEM = """Eres un auditor experto de conversaciones de agentes de IA conversacionales.
Evalúas el desempeño del AGENTE (no del usuario) contra su rol declarado y buenas prácticas.
Sé estricto y objetivo: basa cada juicio en evidencia del transcript.

Devuelve EXCLUSIVAMENTE un JSON con esta forma:
{
  "dimensiones": {
    "adherencia_al_rol":   {"score": 0-100, "razon": "..."},
    "utilidad":            {"score": 0-100, "razon": "..."},
    "manejo_de_alcance":   {"score": 0-100, "razon": "..."},
    "calidez_y_tono":      {"score": 0-100, "razon": "..."},
    "claridad":            {"score": 0-100, "razon": "..."},
    "correctitud_factual": {"score": 0-100, "razon": "..."}
  },
  "findings": [{"severidad": "ALTO|MEDIO|BAJO", "mensaje": "...", "turno": <int|null>}],
  "recomendaciones": ["...", "..."]
}"""

_LLM_USER = """ROL DECLARADO DEL AGENTE: {role}
{prompt_block}
TRANSCRIPT (solo para evaluar al agente):
{transcript}
"""


def _transcript_text(conv: ConversationInput, max_chars: int = 9000) -> str:
    lines = []
    for t in conv.turns:
        who = "AGENTE" if (t.speaker or "").lower() in ("agent", "assistant", "bot") else "USUARIO"
        lines.append(f"[{t.turn_id or '-'}] {who}: {t.message}")
    return "\n".join(lines)[:max_chars]


def run_llm_judge_conversation(conv: ConversationInput, timeout_s: float = 40.0) -> Dict[str, Any]:
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return {"skipped": True, "reason": "sin OPENAI_API_KEY"}
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return {"skipped": True, "reason": "paquete openai no instalado"}

    client = OpenAI(api_key=api_key, timeout=timeout_s)
    prompt_block = f"SYSTEM PROMPT DEL AGENTE:\n{conv.system_prompt[:4000]}\n" if conv.system_prompt else ""
    user_msg = _LLM_USER.format(
        role=conv.agent_role or "(no declarado)",
        prompt_block=prompt_block,
        transcript=_transcript_text(conv),
    )
    try:
        resp = client.chat.completions.create(
            model=settings.JUDGE_MODEL,
            messages=[{"role": "system", "content": _LLM_SYSTEM},
                      {"role": "user", "content": user_msg}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        data["_ok"] = True
        return data
    except Exception as exc:
        log.warning("llm_judge_conversation falló: %s", exc)
        return {"skipped": True, "reason": f"{type(exc).__name__}: {exc}"}


# =============================================================================
# Orquestador
# =============================================================================


def _veredicto(score: float) -> str:
    for nombre, umbral in _VEREDICTOS:
        if score >= umbral:
            return nombre
    return "critico"


def evaluate_conversation(conv: ConversationInput, *, incluir_llm: bool = True) -> ConversationEvalResult:
    met = deterministic_checks(conv)
    criterios = _criterios_deterministicos(conv, met)
    findings: List[ConversationFinding] = []
    dims: Dict[str, Any] = {}
    recomendaciones: List[str] = []
    llm_ok = False

    if not _agent_turns(conv):
        findings.append(ConversationFinding(severidad="ALTO", mensaje="La conversación no tiene turnos del agente."))

    llm_raw = run_llm_judge_conversation(conv) if incluir_llm else {"skipped": True, "reason": "desactivado"}
    if llm_raw.get("_ok"):
        llm_ok = True
        dims = llm_raw.get("dimensiones", {}) or {}
        for f in llm_raw.get("findings", []) or []:
            findings.append(ConversationFinding(
                severidad=str(f.get("severidad", "MEDIO")).upper(),
                mensaje=str(f.get("mensaje", "")), turno=f.get("turno"),
            ))
        recomendaciones = [str(r) for r in (llm_raw.get("recomendaciones") or [])][:6]

    # Score global: si hay LLM, promedio de dimensiones; si no, promedio de criterios determinísticos.
    if dims:
        scores = [float(v.get("score", 0)) for v in dims.values() if isinstance(v, dict)]
        score_global = round(sum(scores) / len(scores), 1) if scores else 0.0
    else:
        ds = [c.score for c in criterios]
        score_global = round(sum(ds) / len(ds), 1) if ds else 0.0

    return ConversationEvalResult(
        conversation_id=conv.conversation_id,
        agent_name=conv.agent_name or "(agente)",
        agent_role=conv.agent_role or "",
        score_global=score_global,
        veredicto=_veredicto(score_global),
        criterios=criterios,
        dimensiones_llm=dims,
        findings=findings,
        metricas=met,
        recomendaciones=recomendaciones,
        llm_judge_aplicado=llm_ok,
        nota_metodo=(
            "Evaluación del transcript: chequeos determinísticos sobre los turnos + "
            + ("LLM-as-judge sobre el rol y conductas del agente. " if llm_ok
               else "LLM-judge NO aplicado (sin OPENAI_API_KEY o desactivado); score basado solo en criterios determinísticos. ")
            + "El prompt_adherence auto-reportado NO se usa como verdad; se evalúa de forma independiente."
        ),
    )


# =============================================================================
# Reporte TXT
# =============================================================================

_L = "=" * 70


def evaluate_agent_conversations(
    agent_name: str, conversations: List["ConversationInput"], *, incluir_llm: bool = True
) -> Dict[str, Any]:
    """QA de un agente sobre VARIAS transcripciones. Reusa evaluate_conversation."""
    from collections import Counter

    results = [evaluate_conversation(c, incluir_llm=incluir_llm) for c in conversations]
    if not results:
        return {"agent_name": agent_name, "n_conversaciones": 0, "score_promedio": 0.0,
                "veredicto": "critico", "por_conversacion": [], "hallazgos_recurrentes": []}

    scores = [r.score_global for r in results]
    prom = round(sum(scores) / len(scores), 1)
    dist = Counter(r.veredicto for r in results)
    msgs = Counter(f.mensaje for r in results for f in r.findings if f.mensaje)

    return {
        "agent_name": agent_name,
        "n_conversaciones": len(results),
        "score_promedio": prom,
        "veredicto": _veredicto(prom),
        "distribucion_veredictos": dict(dist),
        "hallazgos_recurrentes": [{"mensaje": m, "veces": n} for m, n in msgs.most_common(5)],
        "por_conversacion": [
            {"conversation_id": r.conversation_id, "score": r.score_global, "veredicto": r.veredicto}
            for r in results
        ],
        "detalle": [r.model_dump(mode="json") for r in results],
    }


def render_agent_qa_report_txt(qa: Dict[str, Any]) -> str:
    L = [_L, "  QA DE AGENTE POR TRANSCRIPCIONES", "  Lambda Analytics — Juez", _L]
    L.append(f"  Agente             : {qa.get('agent_name')}")
    L.append(f"  Conversaciones     : {qa.get('n_conversaciones')}")
    L.append(f"  Score promedio     : {qa.get('score_promedio')}/100  ({qa.get('veredicto','').upper()})")
    dist = qa.get("distribucion_veredictos") or {}
    if dist:
        L.append("  Distribución       : " + ", ".join(f"{k}={v}" for k, v in dist.items()))
    rec = qa.get("hallazgos_recurrentes") or []
    if rec:
        L.append("")
        L.append("  Problemas recurrentes:")
        for h in rec:
            L.append(f"     ({h['veces']}x) {h['mensaje']}")
    L.append("")
    L.append("  Por conversación:")
    for c in qa.get("por_conversacion", []):
        L.append(f"     [{c['veredicto']}] {c['conversation_id']}: {c['score']}/100")
    L.append(_L)
    return "\n".join(L)


def render_conversation_report(res: ConversationEvalResult) -> str:
    L = [_L, "  EVALUACIÓN DE CONVERSACIÓN", "  Lambda Analytics — Juez", _L]
    L.append(f"  Conversación       : {res.conversation_id}")
    L.append(f"  Agente             : {res.agent_name}  ({res.agent_role})")
    L.append(f"  Veredicto          : {res.veredicto.upper()}   Score: {res.score_global}/100")
    L.append(f"  LLM-judge aplicado : {'sí' if res.llm_judge_aplicado else 'no'}")
    m = res.metricas
    L.append("")
    L.append("  Métricas:")
    L.append(f"     Turnos             : {m.get('total_turnos')} (agente {m.get('turnos_agente')}, usuario {m.get('turnos_usuario')})")
    L.append(f"     Emojis (agente)    : {m.get('emojis_totales')} en {m.get('turnos_agente_con_emoji')} turnos")
    L.append(f"     Tiempo resp. medio : {m.get('tiempo_respuesta_promedio_s')} s")
    L.append(f"     Idioma esperado    : {m.get('idioma_esperado')}")

    if res.dimensiones_llm:
        L.append("")
        L.append("  Dimensiones (LLM-judge):")
        for nombre, v in res.dimensiones_llm.items():
            if isinstance(v, dict):
                L.append(f"     {nombre:<22}: {v.get('score')}/100 — {str(v.get('razon',''))[:90]}")

    if res.criterios:
        L.append("")
        L.append("  Criterios determinísticos:")
        for c in res.criterios:
            estado = "OK" if c.cumple else ("?" if c.cumple is None else "NO")
            L.append(f"     [{estado}] {c.nombre}: {c.detalle}")

    if res.findings:
        L.append("")
        L.append("  Hallazgos:")
        for f in res.findings:
            t = f" (turno {f.turno})" if f.turno else ""
            L.append(f"     [{f.severidad}] {f.mensaje}{t}")

    if res.recomendaciones:
        L.append("")
        L.append("  Recomendaciones:")
        for r in res.recomendaciones:
            L.append(f"     - {r}")

    L.append("")
    L.append(f"  Nota: {res.nota_metodo}")
    L.append(_L)
    return "\n".join(L)
