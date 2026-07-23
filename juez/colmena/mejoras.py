"""Consolidación de un proyecto en un CONTRATO FIRME y propuestas de mejora.

Este módulo toma dos fuentes heterogéneas del Juez —el resultado de La Colmena
(`run_colmena`) y el de las conversaciones (`run_pipeline`)— y las normaliza en
UN solo JSON estable que el consumidor (Gamma) puede leer sin adivinar llaves:

    {
      "kind": "proyecto",
      "nombre": "...",
      "score": 0-100,                 # número único
      "estado": "LISTO" | "NECESITA_AJUSTES" | "NECESITA_ATENCION",
      "resumen_severidad": {critico, alto, medio, bajo, info},
      "problemas": [ {titulo, severidad, descripcion, ubicacion, recomendacion, origen} ],
      "mejoras":   [ {id, titulo, explicacion, severidad, objetivo, antes, despues,
                      aplicable, requiere_revision_manual} ],
      "informe_no_tecnico": "...",  # las mismas `problemas` organizadas en 3
                                     # secciones (seguridad/funcional/tecnico)
                                     # en lenguaje simple para un lector no
                                     # tecnico -- ver juez/evaluation/reporting/legible.py
      "detalle": {"colmena": {...}, "conversaciones": {...}}   # trazabilidad
    }

La clave de la rebanada: `mejoras` incluye —cuando hay prompt— una propuesta con
`antes`/`despues` REALES (el prompt reescrito), no una recomendación suelta. Eso
es lo que hace que el "Aplicar" del lado Gamma pueda ser automático y seguro.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# Variables de plantilla que el prompt puede usar: {{var}}, {var}, [var], ${var}.
# Si la reescritura las pierde, ROMPE el agente en runtime -> hay que detectarlo.
_RE_VARIABLES = re.compile(r"\{\{[^}]+\}\}|\$\{[^}]+\}|\{[^}\s]+\}|\[[^\]\s]+\]")


def _variables_de(texto: str) -> set:
    return set(_RE_VARIABLES.findall(texto or ""))

# Normalización de severidad a la escala interna de La Colmena.
_SEV_NORM = {
    "critical": "critico", "critico": "critico", "crítica": "critico", "critica": "critico",
    "high": "alto", "alto": "alto", "alta": "alto",
    "medium": "medio", "medio": "medio", "media": "medio",
    "low": "bajo", "bajo": "bajo", "baja": "bajo", "minor": "bajo",
    "info": "info",
}
_ORDEN_SEV = {"critico": 0, "alto": 1, "medio": 2, "bajo": 3, "info": 4}


def _norm_sev(s: Any) -> str:
    return _SEV_NORM.get(str(s or "").strip().lower(), "medio")


def _first(d: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return default


def _sin_prefijo_nombre(texto: str) -> str:
    """Quita el prefijo '[nombre]' que La Colmena antepone a las descripciones."""
    t = (texto or "").strip()
    if t.startswith("[") and "]" in t:
        return t.split("]", 1)[1].strip()
    return t


# =============================================================================
# NORMALIZACIÓN DE PROBLEMAS
# =============================================================================


def _problemas_de_colmena(colmena) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for h in (getattr(colmena, "hallazgos", None) or []):
        desc = h.get("descripcion", "")
        out.append({
            "titulo": (_sin_prefijo_nombre(desc) or h.get("obrera", "Hallazgo"))[:120],
            "severidad": _norm_sev(h.get("severidad")),
            "descripcion": desc,
            "ubicacion": h.get("ubicacion", ""),
            "recomendacion": h.get("accion", ""),
            "origen": f"obrera:{h.get('obrera', '')}".rstrip(":"),
        })
    return out


def _problemas_de_conversacion(conversacion: Optional[dict]) -> List[Dict[str, Any]]:
    if not isinstance(conversacion, dict) or conversacion.get("error"):
        return []
    out: List[Dict[str, Any]] = []
    for nodo in conversacion.get("nodos", []) or []:
        nombre_nodo = nodo.get("name", "")
        for p in nodo.get("problemas", []) or []:
            if isinstance(p, str):
                p = {"descripcion": p}
            if not isinstance(p, dict):
                continue
            desc = _first(p, "descripcion", "description", "detalle", "detail",
                          "mensaje", "message", "titulo", "title")
            out.append({
                "titulo": (_first(p, "titulo", "title", "name", default=desc) or "Hallazgo")[:120],
                "severidad": _norm_sev(p.get("severidad") or p.get("severity") or p.get("nivel")),
                "descripcion": desc,
                "ubicacion": nombre_nodo,
                "recomendacion": _first(p, "recomendacion", "recommendation", "accion", "sugerencia"),
                "origen": "conversaciones",
            })
    # Riesgos de coherencia del pipeline.
    for r in ((conversacion.get("coherencia") or {}).get("riesgos") or []):
        if isinstance(r, str):
            r = {"descripcion": r}
        if not isinstance(r, dict):
            continue
        desc = _first(r, "descripcion", "description", "detalle", "mensaje", "message")
        out.append({
            "titulo": (_first(r, "titulo", "title", default=desc) or "Riesgo de coherencia")[:120],
            "severidad": _norm_sev(r.get("severidad") or r.get("severity")),
            "descripcion": desc,
            "ubicacion": "coherencia del pipeline",
            "recomendacion": _first(r, "recomendacion", "recommendation", "accion"),
            "origen": "conversaciones",
        })
    return out


# =============================================================================
# SCORE / ESTADO UNIFICADO
# =============================================================================


def _unificar_score(colmena_score: Optional[float], conv_score: Optional[float]) -> Optional[float]:
    """Combina el score de construcción (Colmena) y el de conversación.

    Si existen ambos, promedio 50/50 (heurística inicial, ajustable). Si solo
    hay uno, ese. Si ninguno, None.
    """
    vals = [v for v in (colmena_score, conv_score) if isinstance(v, (int, float))]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def _estado(score: Optional[float], resumen: Dict[str, int]) -> str:
    hay_graves = resumen.get("critico", 0) > 0 or resumen.get("alto", 0) > 0
    if score is None:
        return "NECESITA_AJUSTES" if hay_graves else "LISTO"
    if score >= 85 and not hay_graves:
        return "LISTO"
    if score >= 70 and not (resumen.get("critico", 0) > 0):
        return "NECESITA_AJUSTES"
    return "NECESITA_ATENCION"


# =============================================================================
# MEJORA CON ANTES/DESPUÉS REAL DEL PROMPT
# =============================================================================


def _es_senal_de_prompt(problema: Dict[str, Any]) -> bool:
    origen = (problema.get("origen") or "").lower()
    return (
        "prompt" in origen
        or "exploradora" in origen
        or "niñera" in origen
        or "ninera" in origen
    )


def _reescribir_prompt(prompt_actual: str, recomendaciones: List[str],
                       senales: List[Dict[str, Any]], api_key: str) -> str:
    """Devuelve una versión mejorada del system prompt (texto plano).

    Reescribe corrigiendo lo detectado, PRESERVANDO intención, idioma y las
    variables {{...}}/{...}. Degrada devolviendo "" ante cualquier problema.
    """
    from juez.llm_client import make_chat_client

    guia = []
    for r in (recomendaciones or [])[:6]:
        guia.append(f"- {r}")
    for s in (senales or [])[:6]:
        desc = s.get("descripcion") or s.get("titulo") or ""
        acc = s.get("recomendacion") or ""
        linea = desc if not acc else f"{desc} → {acc}"
        if linea.strip():
            guia.append(f"- {linea.strip()}")
    guia_txt = "\n".join(guia) or "- Mejorar claridad, guardrails y manejo de casos límite."

    sistema = (
        "Eres un experto en diseño de system prompts para agentes conversacionales. "
        "Te doy el SYSTEM PROMPT ACTUAL de un agente y una lista de PROBLEMAS a corregir. "
        "Reescribe el prompt para corregir esos problemas manteniendo estrictamente: "
        "(1) la intención y el rol original del agente, (2) el mismo idioma, "
        "(3) TODAS las variables tipo {{var}}, {var}, [var] o ${var} EXACTAMENTE como están. "
        "No inventes reglas de negocio nuevas ni cambies el propósito. Mejora claridad, "
        "estructura, guardrails de seguridad y manejo de casos límite. "
        'Responde SOLO JSON: {"prompt_mejorado": "<texto completo del prompt reescrito>"}'
    )
    user = f"PROBLEMAS A CORREGIR:\n{guia_txt}\n\nSYSTEM PROMPT ACTUAL:\n{prompt_actual}"

    client = make_chat_client(api_key=api_key)
    resp = client.chat.completions.create(
        model=os.getenv("JUDGE_MODEL", "gpt-4o-mini"),
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": user}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    import json
    data = json.loads(resp.choices[0].message.content or "{}")
    return str(data.get("prompt_mejorado", "") or "")


def proponer_mejora_prompt(nombre: str, prompt_actual: str,
                           problemas: List[Dict[str, Any]],
                           api_key: str = "") -> Optional[Dict[str, Any]]:
    """Propone una mejora aplicable del prompt con antes/después real, o None."""
    prompt_actual = (prompt_actual or "").strip()
    if not prompt_actual:
        return None
    from juez.llm_client import api_key_presente
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key and not api_key_presente():
        return None

    senales = [p for p in problemas if _es_senal_de_prompt(p)]

    # Diagnóstico determinista del prompt (veredicto + recomendaciones concretas).
    veredicto = ""
    recomendaciones: List[str] = []
    try:
        from prompt_eval.evaluator import evaluate_prompt
        from prompt_eval.models import PromptEvalRequest

        res = evaluate_prompt(PromptEvalRequest(
            prompt=prompt_actual, nombre=nombre or "agente", incluir_llm_judge=False,
        ))
        veredicto = str(getattr(res, "veredicto", "") or "")
        recomendaciones = list(getattr(res, "top_recomendaciones", []) or [])
    except Exception:
        pass

    # Si el prompt ya está bien y no hay señales conversacionales, no proponemos nada.
    if veredicto in ("excelente", "bueno") and not senales:
        return None

    try:
        despues = _reescribir_prompt(prompt_actual, recomendaciones, senales, key).strip()
    except Exception:
        return None
    if not despues or despues == prompt_actual:
        return None

    if senales or veredicto in ("deficiente", "critico"):
        severidad = "alto"
    else:
        severidad = "medio"

    partes = []
    if veredicto:
        partes.append(f"El diagnóstico del prompt fue '{veredicto}'.")
    if senales:
        partes.append(f"Se detectaron {len(senales)} punto(s) en las pruebas del agente.")
    if recomendaciones:
        partes.append("Se aplicaron mejoras de claridad, guardrails y manejo de casos límite.")
    explicacion = " ".join(partes) or "Se propone una versión más clara y segura de las instrucciones del agente."

    # Verificación de seguridad de la reescritura: si el LLM perdió variables de
    # plantilla ({{var}}, {var}, ${var}, [var]) que estaban en el original,
    # aplicar el "después" ROMPERÍA el agente en runtime. En ese caso la mejora
    # NO es auto-aplicable y se marca para revisión humana.
    vars_faltantes = _variables_de(prompt_actual) - _variables_de(despues)
    aplicable = not vars_faltantes
    requiere_revision = bool(vars_faltantes)
    if vars_faltantes:
        explicacion += (
            f" ADVERTENCIA: la reescritura perdió variable(s) de plantilla "
            f"({', '.join(sorted(vars_faltantes))}); requiere revisión manual antes de aplicar."
        )

    return {
        "id": "prompt-rewrite",
        "titulo": "Mejorar las instrucciones del agente",
        "explicacion": explicacion,
        "severidad": severidad,
        "objetivo": {"tipo": "agent", "campo": "prompt"},
        "antes": prompt_actual,
        "despues": despues,
        "aplicable": aplicable,
        "requiere_revision_manual": requiere_revision,
        "variables_perdidas": sorted(vars_faltantes),
    }


# =============================================================================
# CONSOLIDACIÓN
# =============================================================================


def consolidar_proyecto(*, nombre: str, prompt_actual: str = "",
                        colmena=None, conversacion: Optional[dict] = None,
                        openai_key: str = "") -> Dict[str, Any]:
    """Arma el contrato firme del proyecto a partir de Colmena + conversaciones."""
    resumen = {"critico": 0, "alto": 0, "medio": 0, "bajo": 0, "info": 0}

    problemas = _problemas_de_colmena(colmena) + _problemas_de_conversacion(conversacion)
    for p in problemas:
        sev = p["severidad"]
        resumen[sev] = resumen.get(sev, 0) + 1
    problemas.sort(key=lambda p: _ORDEN_SEV.get(p["severidad"], 5))

    colmena_score = getattr(colmena, "score", None) if colmena is not None else None
    conv_score = None
    if isinstance(conversacion, dict) and not conversacion.get("error"):
        cs = conversacion.get("score_general")
        conv_score = float(cs) if isinstance(cs, (int, float)) else None

    score = _unificar_score(colmena_score, conv_score)
    estado = _estado(score, resumen)

    mejoras: List[Dict[str, Any]] = []
    mejora_prompt = proponer_mejora_prompt(nombre, prompt_actual, problemas, openai_key)
    if mejora_prompt:
        mejoras.append(mejora_prompt)

    detalle: Dict[str, Any] = {
        "colmena": colmena.model_dump(mode="json") if colmena is not None else None,
        "conversaciones": conversacion if isinstance(conversacion, dict) else None,
        "score_colmena": colmena_score,
        "score_conversacion": conv_score,
    }

    # Informe no tecnico (3 secciones): presentacion ADICIONAL sobre datos ya
    # calculados arriba -- si falla por lo que sea, nunca debe tumbar el
    # contrato (que ya tiene su score y sus problemas listos).
    informe_no_tecnico = ""
    try:
        from juez.evaluation.reporting.legible import render_informe_no_tecnico
        informe_no_tecnico = render_informe_no_tecnico(
            titulo=f"Evaluación del proyecto: {nombre or 'Proyecto'}",
            veredicto=estado,
            score=score,
            problemas=problemas,
            que_se_evaluo="Seguridad, funcionamiento y construcción técnica del proyecto.",
        )
    except Exception:
        informe_no_tecnico = ""

    return {
        "kind": "proyecto",
        "nombre": nombre or "Proyecto",
        "score": score,
        "estado": estado,
        "resumen_severidad": resumen,
        "problemas": problemas,
        "informe_no_tecnico": informe_no_tecnico,
        "mejoras": mejoras,
        "detalle": detalle,
    }
