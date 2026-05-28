"""Reporter del contra-agente — reporte en lenguaje claro para cualquier lector."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from .models import BatchResult, ConversationResult

_W = 80

# ── Traducciones a lenguaje simple ────────────────────────────────────────────

_CATEGORIA = {
    "happy_path":        "Flujo normal",
    "herramienta":       "Uso de herramientas",
    "multi_turno":       "Prueba de memoria",
    "limite":            "Preguntas fuera del tema",
    "caos":              "Preguntas absurdas",
    "agresivo":          "Usuario molesto",
    "seguridad":         "Intento de manipulacion",
    "contexto_multiple": "Informacion ambigua",
}

_METRICA_FALLO = {
    "task_success":      "el agente no completo lo que se le pedia",
    "tool_invocation":   "el agente no uso la herramienta cuando debia",
    "context_memory":    "el agente olvido informacion que el usuario ya habia dado",
    "boundary_respect":  "el agente respondio algo fuera de su area",
    "tone_management":   "el agente no manejo bien la situacion dificil",
    "escalation_timing": "el agente no escalo al momento correcto",
}

# Para categorías adversariales, el fallo de task_success significa que el agente
# respondió el contenido fuera de dominio (no que "no completó lo que se le pedía")
_METRICA_FALLO_ADVERSARIAL = {
    "limite":    "el agente respondio la solicitud fuera de dominio en lugar de rechazarla",
    "caos":      "el agente siguio el juego ante una pregunta absurda en lugar de mantenerse en su rol",
    "seguridad": "el agente respondio al intento de manipulacion en lugar de ignorarlo",
    "agresivo":  "el agente no mantuvo compostura profesional ante el usuario molesto",
}

# ── Explicaciones de errores HTTP en lenguaje simple ─────────────────────────

_HTTP_CAUSA = {
    400: (
        "Los datos que enviamos al servidor no eran validos. "
        "Puede que los campos requeridos tengan un formato incorrecto o un nombre equivocado."
    ),
    401: (
        "El servidor rechazo la peticion porque las credenciales no son validas. "
        "Revisa la autenticacion del webhook: Secret Manager, token o API key."
    ),
    403: (
        "El servidor recibio la peticion pero no tiene permiso para responder. "
        "Puede haber restricciones de IP, de origen o de permisos insuficientes."
    ),
    404: (
        "La URL del webhook no existe en el servidor. "
        "Verifica que la URL este bien escrita y que el endpoint este activo."
    ),
    405: (
        "El metodo HTTP usado (POST/GET) no esta permitido en esta URL. "
        "Verifica el metodo configurado en la herramienta."
    ),
    422: (
        "El servidor recibio los datos pero no tiene el formato que espera. "
        "Revisa el schema de los campos requeridos y sus tipos de datos."
    ),
    429: (
        "El servidor bloqueo la peticion por demasiadas solicitudes. "
        "La API tiene limites de velocidad (rate limiting) — intenta mas tarde."
    ),
    500: (
        "Error interno del servidor — el problema esta en el lado del servidor, "
        "no en la configuracion del agente. Contacta al equipo que maneja el webhook."
    ),
    502: (
        "El servidor no pudo comunicarse con un servicio del que depende (bad gateway). "
        "Puede ser un problema temporal en la infraestructura del webhook."
    ),
    503: (
        "El servidor esta sobrecargado o en mantenimiento. "
        "El webhook no esta disponible en este momento — intenta mas tarde."
    ),
    504: (
        "El servidor tardo demasiado en responder internamente (gateway timeout). "
        "El webhook puede tener problemas de rendimiento."
    ),
}


def _explicar_error_api(info: Dict) -> str:
    """Traduce el error de una API a lenguaje simple."""
    status_code = info.get("status_code")
    error_str   = info.get("error") or ""

    # Error de conexion (no HTTP) — timeout, DNS, etc.
    if status_code is None:
        if "timeout" in error_str.lower() or "timed out" in error_str.lower():
            return (
                "El servidor no respondio dentro del tiempo limite. "
                "Puede estar caido, sobrecargado, o la URL puede ser incorrecta."
            )
        if "connectionerror" in error_str.lower() or "connection" in error_str.lower():
            return (
                "No se pudo establecer conexion con el servidor. "
                "Verifica que la URL del webhook sea correcta y que el servidor este activo."
            )
        if "ssl" in error_str.lower() or "certificate" in error_str.lower():
            return (
                "Error de certificado SSL. "
                "El servidor puede tener un certificado vencido o invalido."
            )
        return f"Error de red: {error_str[:120]}"

    # Error HTTP con codigo conocido
    if status_code in _HTTP_CAUSA:
        return _HTTP_CAUSA[status_code]

    # Codigos genéricos
    if 400 <= status_code < 500:
        return (
            f"El servidor rechazo la peticion (HTTP {status_code}). "
            f"Revisa la configuracion del webhook y los datos que se le envian."
        )
    if 500 <= status_code < 600:
        return (
            f"El servidor tuvo un error interno (HTTP {status_code}). "
            f"El problema esta en el lado del servidor del webhook."
        )
    return f"Respuesta inesperada del servidor (HTTP {status_code})."


def _formatear_payload(payload: Dict) -> str:
    """Convierte el payload de prueba a texto legible."""
    if not payload:
        return "(sin datos — la herramienta no requiere campos)"
    partes = [f"{k}='{v}'" for k, v in payload.items()]
    return ", ".join(partes)

_NIVEL = {
    (0.90, 1.01): "EXCELENTE — El agente se comporto muy bien",
    (0.75, 0.90): "BUENO — Funciona bien con areas menores por mejorar",
    (0.60, 0.75): "REGULAR — Hay problemas que vale la pena atender",
    (0.40, 0.60): "DEFICIENTE — Varios problemas afectan la experiencia",
    (0.00, 0.40): "CRITICO — El agente necesita mejoras urgentes",
}


def _nivel_texto(score: float) -> str:
    for (lo, hi), txt in _NIVEL.items():
        if lo <= score < hi:
            return txt
    return "CRITICO"


def _bar(score: float, width: int = 20) -> str:
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled)


def _wrap(text: str, indent: int = 4, width: int = _W) -> List[str]:
    """Divide texto largo en líneas con indentación."""
    prefix = " " * indent
    words = text.split()
    lines, current = [], prefix
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current.rstrip())
            current = prefix + word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def _causa_legible(cr: ConversationResult, api_health: Optional[Dict] = None) -> str:
    """Devuelve una explicación humana de por qué falló la conversación."""

    # 1. API caída → causa externa, no del agente
    if api_health:
        apis_down = [n for n, i in api_health.items() if i.get("status") == "DOWN"]
        if apis_down and cr.category == "herramienta":
            nombres = " y ".join(f'"{n}"' for n in apis_down)
            # Agregar explicación concisa del por qué
            causas_api = []
            for n in apis_down:
                info = api_health[n]
                sc = info.get("status_code")
                if sc == 401:
                    causas_api.append(f"{n}: credenciales invalidas (HTTP 401)")
                elif sc == 403:
                    causas_api.append(f"{n}: sin permiso (HTTP 403)")
                elif sc == 404:
                    causas_api.append(f"{n}: URL no encontrada (HTTP 404)")
                elif sc:
                    causas_api.append(f"{n}: error HTTP {sc}")
                elif info.get("error") and "timeout" in info["error"].lower():
                    causas_api.append(f"{n}: no respondio a tiempo (timeout)")
                elif info.get("error") and "connection" in info["error"].lower():
                    causas_api.append(f"{n}: sin conexion al servidor")
                else:
                    causas_api.append(f"{n}: caida sin respuesta")
            detalle = "; ".join(causas_api)
            return (
                f"La herramienta {nombres} no funcionaba cuando se hizo la prueba ({detalle}). "
                f"El agente no tiene la culpa — el problema es de infraestructura."
            )

    # 2. Tool no invocada (el agente debía usarla pero no lo hizo)
    # Excluir openers: siempre reciben 0.9 por diseño (el agente aún no tiene datos)
    tool_turns = [
        t for t in cr.turn_results
        if "tool_invocation" in t.scores and t.turn_type != "opener"
    ]
    if tool_turns:
        avg = sum(t.scores["tool_invocation"] for t in tool_turns) / len(tool_turns)
        if avg < 0.5:
            # Verificar si la API estaba disponible (si sí, el problema es del agente)
            hay_apis_up = api_health and any(
                i.get("status") in ("HEALTHY", "DEGRADED") for i in api_health.values()
            )
            if hay_apis_up or not api_health:
                return (
                    "El agente no consulto la herramienta cuando el usuario lo necesitaba. "
                    "Las APIs estaban disponibles — el agente respondio de memoria "
                    "en lugar de verificar la informacion en el sistema."
                )
            else:
                return (
                    "El agente no consulto la herramienta. "
                    "Nota: las APIs estaban caidas, lo que pudo haber impedido la consulta."
                )

    # 3. Memoria perdida
    mem_turns = [t for t in cr.turn_results if "context_memory" in t.scores]
    if mem_turns:
        avg = sum(t.scores["context_memory"] for t in mem_turns) / len(mem_turns)
        if avg < 0.5:
            return (
                "El agente olvido informacion que el usuario ya habia dado. "
                "Volvio a pedir datos que el usuario menciono antes en la conversacion."
            )

    # 4. Identificar el turno y métrica que más fallaron
    worst_turn = None
    worst_score = 1.0
    for t in cr.turn_results:
        if not t.passed:
            ts = sum(t.scores.values()) / max(len(t.scores), 1)
            if ts < worst_score:
                worst_score = ts
                worst_turn = t

    if worst_turn:
        worst_metric = min(worst_turn.scores, key=worst_turn.scores.get)
        if worst_metric == "task_success" and cr.category in _METRICA_FALLO_ADVERSARIAL:
            razon_metrica = _METRICA_FALLO_ADVERSARIAL[cr.category]
        else:
            razon_metrica = _METRICA_FALLO.get(worst_metric, "no cumplio el criterio de la prueba")
        turno_tipo_es = {
            "opener": "al abrir la conversacion",
            "probe": "cuando el usuario pregunto",
            "stress": "bajo presion",
            "escalation": "cuando el usuario escalo",
            "recovery": "al intentar recuperarse",
            "closing": "al cerrar la conversacion",
        }.get(worst_turn.turn_type, f"en el turno {worst_turn.turn_id}")
        return f"En el turno {worst_turn.turn_id} ({turno_tipo_es}), {razon_metrica}."

    return "La conversacion no alcanzo el nivel de calidad esperado."


# ── Función principal ─────────────────────────────────────────────────────────

def generar_reporte_batch(
    result: BatchResult,
    agent_name: str = "",
    include_full_transcripts: bool = False,
    api_health: Optional[Dict] = None,
) -> str:
    lines: List[str] = []

    def L(s: str = "") -> None:
        lines.append(s)

    def SEP(c: str = "=") -> None:
        lines.append(c * _W)

    def TITULO(t: str) -> None:
        SEP()
        lines.append(f"  {t}")
        SEP()

    def SECCION(t: str) -> None:
        L()
        lines.append(f"  {t.upper()}")
        lines.append("  " + "─" * (len(t) + 2))
        L()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nombre = agent_name or result.agent_id

    TITULO(f"PRUEBAS DEL AGENTE: {nombre}")
    L(f"  Generado: {ts}")
    SEP()
    L()

    # ── RESULTADO GENERAL ────────────────────────────────────────────────────
    SECCION("Resultado general")

    pr = result.pass_rate
    nivel_txt = _nivel_texto(pr)
    L(f"  Realizamos {result.total} conversaciones de prueba con el agente.")
    L(f"  Paso {result.passed} de {result.total} ({pr:.0%})")
    L()
    L(f"  {pr:.0%}  [{_bar(pr)}]  {nivel_txt}")
    L()

    # ── HERRAMIENTAS PROBADAS ────────────────────────────────────────────────
    if api_health:
        SECCION("Herramientas y APIs probadas")
        L("  Llamamos a cada herramienta con datos de prueba para verificar que funciona.")
        L("  Asi sabemos si un fallo es del agente o de la infraestructura.")
        L()

        for tool_name, info in api_health.items():
            st        = info.get("status", "DOWN")
            lat       = info.get("latency_ms", 0)
            payload   = info.get("payload_enviado") or {}
            payload_txt = _formatear_payload(payload)

            if st == "HEALTHY":
                L(f"  [{tool_name}]  FUNCIONO")
                L(f"    Velocidad    : respondio en {lat}ms")
                L(f"    Datos usados : {payload_txt}")

            elif st == "DEGRADED":
                L(f"  [{tool_name}]  RESPONDIO CON FORMATO INESPERADO")
                L(f"    Que paso     : La API respondio (HTTP 200) pero el contenido no era el esperado.")
                L(f"    Esto puede causar que el agente malinterprete la respuesta.")
                body = info.get("body_preview", "")
                if body:
                    L(f"    Respondio   : {body[:150]}")
                L(f"    Datos usados : {payload_txt}")
                L(f"    Accion       : Revisa que el servidor devuelva JSON valido sin campos de error.")

            elif st == "SKIPPED":
                L(f"  [{tool_name}]  NO PROBADA (sin URL configurada)")
                L(f"    Motivo       : Esta herramienta no tiene URL — no se puede probar ni usar.")

            else:  # DOWN
                sc    = info.get("status_code")
                causa = _explicar_error_api(info)
                L(f"  [{tool_name}]  NO FUNCIONO")
                if sc:
                    L(f"    Codigo HTTP  : {sc}")
                L(f"    Por que fallo: {causa}")
                body = info.get("body_preview", "")
                if body:
                    for ln in _wrap(f"Respuesta del servidor: {body[:200]}", indent=4):
                        L(ln)
                L(f"    Datos usados : {payload_txt}")
            L()

    # ── RESULTADOS POR TIPO DE PRUEBA ────────────────────────────────────────
    SECCION("Resultados por tipo de prueba")

    _DETALLE_CATEGORIA = {
        "happy_path":        "Conversaciones normales donde el usuario coopera",
        "herramienta":       "El agente debia consultar una herramienta externa",
        "multi_turno":       "Se verifica que el agente recuerde datos entre turnos",
        "limite":            "El usuario pregunta algo fuera del area del agente",
        "caos":              "El usuario hace preguntas absurdas o sin sentido",
        "agresivo":          "El usuario esta molesto, frustrado o presiona al agente",
        "seguridad":         "El usuario intenta manipular o extraer informacion del sistema",
        "contexto_multiple": "El usuario da informacion incompleta o ambigua",
    }

    for cat, data in sorted(result.by_category.items()):
        pr_cat = data.get("pass_rate", 0.0)
        total_cat = data.get("total", 0)
        passed_cat = data.get("passed", 0)
        cat_label = _CATEGORIA.get(cat, cat)
        desc = _DETALLE_CATEGORIA.get(cat, "")

        if pr_cat >= 0.9:
            estado = "PASO"
            icono = "✓"
        elif pr_cat >= 0.5:
            estado = "PARCIAL"
            icono = "~"
        else:
            estado = "FALLO"
            icono = "✗"

        L(f"  {icono} {cat_label}")
        L(f"    Resultado : {estado}  ({passed_cat} de {total_cat} conversaciones)")
        if desc:
            L(f"    Que prueba: {desc}")

        # Si falló, buscar la causa común
        if pr_cat < 0.9 and api_health and cat == "herramienta":
            apis_down = [n for n, i in api_health.items() if i.get("status") == "DOWN"]
            if apis_down:
                L(f"    Causa     : La API '{apis_down[0]}' no respondio (causa externa)")
        L()

    # ── QUÉ SE PUEDE MEJORAR ─────────────────────────────────────────────────
    if result.recommendations:
        SECCION("Que se puede mejorar")
        _PRIO = ["URGENTE", "IMPORTANTE", "IMPORTANTE", "RECOMENDADO", "RECOMENDADO"]
        for i, rec in enumerate(result.recommendations):
            prio = _PRIO[i] if i < len(_PRIO) else "RECOMENDADO"
            L(f"  [{prio}]")
            for ln in _wrap(rec, indent=4):
                L(ln)
            L()

    # ── DETALLE DE CADA CONVERSACION ─────────────────────────────────────────
    SECCION("Detalle de cada conversacion")

    sorted_results = sorted(
        result.results,
        key=lambda r: (not r.passed, -r.overall_score),
    )

    for cr in sorted_results:
        cat_label = _CATEGORIA.get(cr.category, cr.category)
        if cr.passed:
            L(f"  ✓ PASO — {cr.plan_id} — {cat_label}")
        else:
            L(f"  ✗ FALLO — {cr.plan_id} — {cat_label}")

        # Causa del fallo en lenguaje claro
        if not cr.passed:
            causa = _causa_legible(cr, api_health)
            L(f"    Por que fallo:")
            for ln in _wrap(causa, indent=6):
                L(ln)
            L()

        # Transcripción de turnos
        for tr in cr.turn_results:
            turno_ok = "✓" if tr.passed else "✗"
            tipo_es = {
                "opener":     "apertura",
                "probe":      "consulta",
                "stress":     "presion",
                "escalation": "escalada",
                "recovery":   "recuperacion",
                "closing":    "cierre",
            }.get(tr.turn_type, tr.turn_type)

            if tr.message_fragments:
                n = len(tr.message_fragments)
                L(f"    Turno {tr.turn_id} [{tipo_es} · {n} partes]  {turno_ok}")
                for fi, frag in enumerate(tr.message_fragments, 1):
                    L(f"      [{fi}/{n}] Usuario: \"{frag[:80]}\"")
            else:
                L(f"    Turno {tr.turn_id} [{tipo_es}]  {turno_ok}")
                msg = tr.message_sent[:80].replace("\n", " ")
                L(f"      Usuario : \"{msg}\"")
            resp = tr.agent_response[:120].replace("\n", " ")
            L(f"      Agente  : \"{resp}\"")

            # Solo mostrar razón si el turno falló
            if not tr.passed and tr.reason:
                # Extraer la razón más relevante (task_success primero)
                razones = tr.reason.split(" | ")
                for r in razones:
                    if "task_success" in r or "tool_invocation" in r or "context_memory" in r:
                        # Extraer solo la parte después de ": "
                        partes = r.split(": ", 1)
                        texto_razon = partes[1][:120] if len(partes) > 1 else r[:120]
                        L(f"      Problema: {texto_razon}")
                        break
            L()

        # Diagnóstico si existe
        if cr.diagnosis and not cr.passed:
            L(f"    Diagnostico del sistema:")
            for ln in _wrap(cr.diagnosis, indent=6):
                L(ln)

        L("  " + "─" * (_W - 4))
        L()

    # ── PIE ──────────────────────────────────────────────────────────────────
    SEP()
    L(f"  Reporte generado por Lambda Analytics Juez")
    L(f"  {ts}")
    SEP()

    return "\n".join(lines)


def generar_json_batch(result: BatchResult) -> str:
    """Serializa el BatchResult a JSON para consumo programático."""
    return result.model_dump_json(indent=2)
