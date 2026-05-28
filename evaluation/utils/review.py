"""review.py — Revisión conversacional de reglas_negocio y configuración de evaluación.

Dos funciones principales:
  - revisar_reglas_negocio()       : el usuario edita las reglas en lenguaje natural
  - configurar_evaluacion_conversacional() : el usuario configura casos y distribución
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    _console = Console(highlight=False, emoji=False)
    HAS_RICH = True
except ImportError:
    _console = None  # type: ignore
    HAS_RICH = False

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


_SYSTEM_REVISOR = """\
Eres un asistente que ayuda a revisar y editar las reglas de negocio de un agente de IA conversacional \
antes de que se generen escenarios de prueba.

Recibirás un JSON con las reglas actuales y un mensaje del usuario describiendo qué quiere hacer.

ESTRUCTURA DEL JSON (no cambies los nombres de campo):
{
  "enfoque":               string  — párrafo 2-3 oraciones sobre el propósito del agente,
  "dominio":               string  — una línea describiendo el agente,
  "no_puede":              lista   — cosas que el agente tiene PROHIBIDO hacer,
  "reglas_clave":          lista   — reglas de negocio importantes para la evaluación,
  "casos_limite_criticos": lista   — situaciones que pondrían a prueba los límites del agente,
  "instrucciones_extra":   string  — directrices estratégicas libres del evaluador sobre cómo generar los escenarios
}

TU RESPUESTA debe ser siempre JSON válido con este formato:
{
  "accion": "continuar" | "editar" | "aclarar",
  "reglas": { <JSON actualizado, solo si accion=editar; omite este campo si accion=aclarar o continuar> },
  "mensaje": "Explicación breve en español de lo que hiciste, o la pregunta de aclaración si accion=aclarar"
}

REGLAS:
- Si el usuario dice que todo está bien, que continúe, "listo", "ok", "sí", "perfecto", "sin cambios", etc. → accion="continuar"
- Si el usuario pide editar un campo específico (enfoque, prohibición, regla, caso límite) → modifica ese campo y accion="editar"
- Si el usuario da una DIRECTRIZ ESTRATÉGICA sobre cómo generar los escenarios → escríbela en `instrucciones_extra` y accion="editar"
- Si el usuario hace referencia a algo de la conversación anterior que puedes resolver con el historial → resuélvelo y accion="editar"
- Si el usuario pide algo AMBIGUO que no puedes resolver sin más contexto (ej: "vuelve a meter la que quitaste" y no hay historial) → accion="aclarar" con una pregunta concreta en "mensaje". NUNCA confirmes ni edites cuando no entiendes qué quiere el usuario.
- Puedes entender referencias como "la prohibición 2", "el primer caso límite", "el enfoque", etc.
- Nunca agregues campos que no estén en la estructura definida arriba
- Mantén el idioma español en los contenidos
"""


def _ask(prompt_text: str, default: str = "") -> str:
    try:
        if HAS_RICH:
            val = _console.input(f"[cyan]{prompt_text}[/cyan]")
        else:
            val = input(prompt_text)
        return val.strip() or default
    except EOFError:
        # stdin agotado (ejecucion no interactiva / pipe) — confirmar automaticamente
        return default or "listo"


def _mostrar_reglas(r: Dict) -> None:
    if HAS_RICH:
        _console.print("\n[bold yellow]REGLAS DE NEGOCIO[/bold yellow]\n")
        dominio = r.get("dominio", "")
        enfoque = r.get("enfoque", "")
        instrucciones = r.get("instrucciones_extra", "")
        if dominio:
            _console.print(f"  [bold]Dominio:[/bold] {dominio}")
        if enfoque:
            _console.print(f"\n  [bold]Enfoque:[/bold]")
            _console.print(f"    {enfoque}")
        for campo, etiqueta in [
            ("no_puede",              "Prohibiciones"),
            ("reglas_clave",          "Reglas clave"),
            ("casos_limite_criticos", "Casos limite criticos"),
        ]:
            items = r.get(campo, [])
            if items:
                _console.print(f"\n  [bold]{etiqueta}:[/bold]")
                for i, item in enumerate(items, 1):
                    _console.print(f"    {i}. {item}")
        if instrucciones:
            _console.print(f"\n  [bold]Directrices de generacion:[/bold]")
            _console.print(f"    [italic]{instrucciones}[/italic]")
    else:
        print("\nREGLAS DE NEGOCIO\n")
        dominio = r.get("dominio", "")
        enfoque = r.get("enfoque", "")
        instrucciones = r.get("instrucciones_extra", "")
        if dominio:
            print(f"  Dominio: {dominio}")
        if enfoque:
            print(f"\n  Enfoque:\n    {enfoque}")
        for campo, etiqueta in [
            ("no_puede",              "Prohibiciones"),
            ("reglas_clave",          "Reglas clave"),
            ("casos_limite_criticos", "Casos limite criticos"),
        ]:
            items = r.get(campo, [])
            if items:
                print(f"\n  {etiqueta}:")
                for i, item in enumerate(items, 1):
                    print(f"    {i}. {item}")
        if instrucciones:
            print(f"\n  Directrices de generacion:\n    {instrucciones}")


def _aplicar_con_gpt(
    reglas: Dict,
    instruccion: str,
    openai_key: str,
    historial: Optional[List[Dict]] = None,
) -> tuple[Dict, str, str]:
    """Llama a GPT con historial y devuelve (reglas_actualizadas, mensaje, accion).

    accion es "editar", "continuar" o "aclarar".
    """
    client = OpenAI(api_key=openai_key)

    # El primer mensaje de usuario siempre incluye el estado actual de las reglas
    estado_msg = (
        f"Estado actual de las reglas:\n{json.dumps(reglas, ensure_ascii=False, indent=2)}\n\n"
        f"El usuario dice: {instruccion}"
    )

    # Construir mensajes: sistema + historial previo + mensaje actual
    messages: List[Dict] = [{"role": "system", "content": _SYSTEM_REVISOR}]
    for h in (historial or []):
        messages.append({"role": "user",      "content": h["user"]})
        messages.append({"role": "assistant", "content": h["assistant"]})
    messages.append({"role": "user", "content": estado_msg})

    r = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=1500,
    )
    raw_text = r.choices[0].message.content or "{}"
    raw = json.loads(raw_text)
    accion  = raw.get("accion", "aclarar")
    mensaje = raw.get("mensaje", "")
    nuevas  = raw.get("reglas", None)

    if accion == "editar" and isinstance(nuevas, dict):
        return nuevas, mensaje, "editar"
    if accion == "continuar":
        return reglas, mensaje, "continuar"
    # "aclarar" o cualquier otro valor desconocido → pedir aclaración sin tocar reglas
    return reglas, mensaje or "¿Podrías ser más específico? No entendí bien qué querías cambiar.", "aclarar"


def revisar_reglas_negocio(reglas: Dict, openai_key: str = "") -> Dict:
    """Muestra las reglas extraídas y permite al usuario editarlas conversacionalmente."""

    if HAS_RICH:
        _console.print("\n[bold cyan]" + "═" * 60 + "[/bold cyan]")
        _console.print("[bold white]   REVISION DE REGLAS DE NEGOCIO[/bold white]")
        _console.print("[bold cyan]" + "═" * 60 + "[/bold cyan]")
    else:
        print("\n" + "=" * 60)
        print("   REVISION DE REGLAS DE NEGOCIO")
        print("=" * 60)

    if not reglas or reglas.get("error"):
        if HAS_RICH:
            _console.print("[yellow]  No se extrajeron reglas del system prompt.[/yellow]")
        else:
            print("  No se extrajeron reglas del system prompt.")
        resp = _ask("¿Continuar de todas formas? [S/n]: ", "s").lower()
        if resp in ("n", "no"):
            reglas = {
                "enfoque": _ask("  Describe el propósito del agente: ", ""),
                "dominio":  _ask("  Dominio (una línea): ", ""),
                "no_puede": [],
                "reglas_clave": [],
                "casos_limite_criticos": [],
            }
        return reglas

    _mostrar_reglas(reglas)

    puede_conversar = HAS_OPENAI and bool(openai_key)

    if HAS_RICH:
        if puede_conversar:
            _console.print(
                "\n[dim]Dime qué quieres cambiar, agregar o eliminar — "
                "o escribe 'listo' para continuar.[/dim]"
            )
        else:
            _console.print("\n[dim]Escribe 'listo' para continuar.[/dim]")
    else:
        hint = "Dime qué cambiar — o escribe 'listo' para continuar." if puede_conversar else "Escribe 'listo' para continuar."
        print(f"\n  {hint}")

    historial: List[Dict] = []  # historial de turnos para contexto entre llamadas

    _listo = {"listo", "continuar", "ok", "bien", "correcto", "perfecto",
              "sí", "si", "yes", "c", "adelante", "proceder", "ya"}
    _listo_frases = ("todo bien", "está bien", "todo correcto", "sin cambios",
                     "así está", "así me gusta", "lo dejamos", "me parece bien",
                     "se ve bien", "todo ok")

    while True:
        user_input = _ask("\n  Tú: ", "").strip()

        if not user_input:
            continue

        # Confirmaciones explícitas del usuario — no necesitan GPT
        if user_input.lower() in _listo or any(kw in user_input.lower() for kw in _listo_frases):
            break

        if not puede_conversar:
            if HAS_RICH:
                _console.print("[yellow]  Sin OpenAI API key — escribe 'listo' para continuar.[/yellow]")
            else:
                print("  Sin OpenAI API key — escribe 'listo' para continuar.")
            continue

        # Llamada a GPT con historial
        try:
            if HAS_RICH:
                _console.print("[dim]  ...[/dim]")
            reglas_nuevas, mensaje, accion = _aplicar_con_gpt(
                reglas, user_input, openai_key, historial=historial
            )
        except Exception as exc:
            if HAS_RICH:
                _console.print(f"[red]  Error: {exc}[/red]")
            else:
                print(f"  Error: {exc}")
            continue

        if HAS_RICH:
            color = "green" if accion == "editar" else ("yellow" if accion == "aclarar" else "green")
            _console.print(f"\n[{color}]  Juez:[/{color}] {mensaje}")
        else:
            print(f"\n  Juez: {mensaje}")

        # Guardar turno en historial (compacto para no inflar el contexto)
        historial.append({
            "user": f"El usuario dice: {user_input}",
            "assistant": json.dumps({"accion": accion, "mensaje": mensaje}, ensure_ascii=False),
        })
        # Mantener historial corto (últimos 6 turnos)
        if len(historial) > 6:
            historial = historial[-6:]

        if accion == "continuar":
            break

        if accion == "aclarar":
            # GPT pidió aclaración — no cambiar reglas, no cerrar, seguir esperando respuesta
            continue

        # accion == "editar"
        reglas = reglas_nuevas
        _mostrar_reglas(reglas)

        if HAS_RICH:
            _console.print("\n[dim]¿Algo más que quieras cambiar?[/dim]")
        else:
            print("\n  ¿Algo más que quieras cambiar?")

    if HAS_RICH:
        _console.print("\n[green]  Reglas confirmadas.[/green]\n")
    else:
        print("\n  Reglas confirmadas.\n")

    return reglas


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE EVALUACIÓN — número de casos y distribución
# ─────────────────────────────────────────────────────────────────────────────

_PESOS_DEFAULT: Dict[str, float] = {
    "happy_path":        0.25,
    "herramienta":       0.20,
    "multi_turno":       0.15,
    "limite":            0.10,
    "caos":              0.10,
    "agresivo":          0.10,
    "seguridad":         0.05,
    "contexto_multiple": 0.05,
}

_SYSTEM_CONFIGURADOR = """\
Eres un asistente que ayuda a configurar una evaluación de un agente de IA conversacional.

El usuario puede pedir ajustes en lenguaje natural: cuántas conversaciones, qué categorías \
priorizar, escenarios adicionales que incluir, etc.

Categorías disponibles:
  happy_path, herramienta, multi_turno, limite, caos, agresivo, seguridad, contexto_multiple

TU RESPUESTA debe ser siempre JSON con este formato:
{
  "accion": "confirmar" | "ajustar",
  "total": <entero 5-50>,
  "distribucion": {
    "happy_path": N,
    "herramienta": N,
    "multi_turno": N,
    "limite": N,
    "caos": N,
    "agresivo": N,
    "seguridad": N,
    "contexto_multiple": N
  },
  "escenarios_extra": ["descripción libre de escenario adicional", ...],
  "mensaje": "Explicación breve en español de lo que ajustaste"
}

REGLAS:
- La distribución SIEMPRE debe sumar exactamente `total`. Verifica la suma antes de responder.
- Nunca dejes una categoría en negativo.
- Si el usuario confirma ("listo", "ok", "sí", "bien", "adelante", "perfecto", "así está bien") → accion="confirmar"
- Si pide un total diferente ("quiero 25", "ponle 30") → cambia total y recalcula distribución proporcional manteniendo TODAS las categorías que ya existían.
- Si pide un escenario específico ("incluye uno donde el cliente ya llamó antes") → agrégalo a escenarios_extra.
- Si pide énfasis en ciertas categorías ("más agresivos", "enfócate en limite y caos"):
    → AUMENTA esas categorías quitando conversaciones de las de mayor peso (normalmente happy_path, herramienta).
    → NUNCA elimines completamente una categoría que ya estaba en la distribución actual — como mínimo déjala en 1.
    → Distribuye la reducción entre las categorías no priorizadas de forma proporcional.
- Si el usuario dice que no le gustó un cambio ("no quiero eso", "vuelve como estaba") → restaura la distribución anterior y accion="ajustar".
- Mantén el idioma español en los mensajes.
"""


def _calcular_distribucion_default(total: int) -> Dict[str, int]:
    dist = {cat: round(total * p) for cat, p in _PESOS_DEFAULT.items()}
    dist = {cat: n for cat, n in dist.items() if n > 0}
    if not dist:
        dist = {"happy_path": total}
    diff = total - sum(dist.values())
    if diff != 0:
        top = max(dist, key=lambda c: _PESOS_DEFAULT.get(c, 0))
        dist[top] = max(0, dist[top] + diff)
    return dist


def _mostrar_distribucion(total: int, distribucion: Dict[str, int], escenarios_extra: List[str]) -> None:
    max_n = max(distribucion.values()) if distribucion else 1
    bar_width = 20

    if HAS_RICH:
        _console.print(f"\n  [bold]Total:[/bold] {total} conversaciones\n")
        _console.print("  [bold]Distribución:[/bold]")
        for cat, n in sorted(distribucion.items(), key=lambda x: -x[1]):
            filled = round(n / max_n * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            _console.print(f"    [cyan]{cat:<20}[/cyan] {bar} {n:>2}")
        if escenarios_extra:
            _console.print(f"\n  [bold]Escenarios adicionales:[/bold]")
            for e in escenarios_extra:
                _console.print(f"    + {e}")
    else:
        print(f"\n  Total: {total} conversaciones\n")
        print("  Distribución:")
        for cat, n in sorted(distribucion.items(), key=lambda x: -x[1]):
            filled = round(n / max_n * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            print(f"    {cat:<20} {bar} {n:>2}")
        if escenarios_extra:
            print("\n  Escenarios adicionales:")
            for e in escenarios_extra:
                print(f"    + {e}")


def _ajustar_con_gpt(
    total: int,
    distribucion: Dict[str, int],
    escenarios_extra: List[str],
    instruccion: str,
    openai_key: str,
    historial: Optional[List[Dict]] = None,
) -> tuple[int, Dict[str, int], List[str], str, bool]:
    """Llama a GPT con historial y devuelve (total, distribucion, escenarios, mensaje, confirmar)."""
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)

    estado = {
        "total": total,
        "distribucion": distribucion,
        "escenarios_extra": escenarios_extra,
        "categorias_disponibles": list(_PESOS_DEFAULT.keys()),
    }
    user_msg = (
        f"Configuración actual:\n{json.dumps(estado, ensure_ascii=False, indent=2)}\n\n"
        f"IMPORTANTE: mantén al menos 1 conversación en cada categoría que ya existe.\n\n"
        f"El usuario dice: {instruccion}"
    )

    messages: List[Dict] = [{"role": "system", "content": _SYSTEM_CONFIGURADOR}]
    for h in (historial or []):
        messages.append({"role": "user",      "content": h["user"]})
        messages.append({"role": "assistant", "content": h["assistant"]})
    messages.append({"role": "user", "content": user_msg})

    r = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=800,
    )
    raw = json.loads(r.choices[0].message.content or "{}")
    accion      = raw.get("accion", "ajustar")
    nuevo_total = int(raw.get("total", total))
    nueva_dist  = {k: int(v) for k, v in raw.get("distribucion", distribucion).items() if int(v) > 0}
    nuevos_esc  = raw.get("escenarios_extra", escenarios_extra)
    mensaje     = raw.get("mensaje", "")

    # Asegurar que la suma cuadre con el total
    suma = sum(nueva_dist.values())
    if suma != nuevo_total and nueva_dist:
        diff = nuevo_total - suma
        top = max(nueva_dist, key=lambda c: _PESOS_DEFAULT.get(c, 0))
        nueva_dist[top] = max(0, nueva_dist[top] + diff)

    return nuevo_total, nueva_dist, nuevos_esc, mensaje, accion == "confirmar"


def configurar_evaluacion_conversacional(openai_key: str = "") -> Dict[str, Any]:
    """Pregunta conversacionalmente cuántos casos y qué distribución quiere el usuario.

    Retorna:
        total         : int
        distribucion  : Dict[str, int]
        escenarios_extra : List[str]
        concurrencia  : int
    """
    if HAS_RICH:
        _console.print("\n[bold cyan]" + "═" * 60 + "[/bold cyan]")
        _console.print("[bold white]   CONFIGURACION DE LA EVALUACION[/bold white]")
        _console.print("[bold cyan]" + "═" * 60 + "[/bold cyan]")
    else:
        print("\n" + "=" * 60)
        print("   CONFIGURACION DE LA EVALUACION")
        print("=" * 60)

    # Preguntar primero cuántas conversaciones
    while True:
        raw = _ask("\n  ¿Cuántas conversaciones quieres generar? (5-50) [20]: ", "20").strip()
        try:
            total = int(raw)
            if 5 <= total <= 50:
                break
            if HAS_RICH:
                _console.print("[yellow]  Debe ser un número entre 5 y 50.[/yellow]")
            else:
                print("  Debe ser un número entre 5 y 50.")
        except ValueError:
            if HAS_RICH:
                _console.print("[yellow]  Ingresa un número válido.[/yellow]")
            else:
                print("  Ingresa un número válido.")

    distribucion = _calcular_distribucion_default(total)
    escenarios_extra: List[str] = []

    _mostrar_distribucion(total, distribucion, escenarios_extra)

    puede_conversar = HAS_OPENAI and bool(openai_key)

    if HAS_RICH:
        if puede_conversar:
            _console.print(
                "\n[dim]¿Quieres ajustar la distribución o agregar escenarios específicos? "
                "Dímelo, o escribe 'listo' para confirmar.[/dim]"
            )
        else:
            _console.print("\n[dim]Escribe 'listo' para confirmar.[/dim]")
    else:
        if puede_conversar:
            print("\n  ¿Quieres ajustar la distribución o agregar escenarios? Di qué cambiar, o 'listo' para confirmar.")
        else:
            print("\n  Escribe 'listo' para confirmar.")

    # Solo palabras inequívocamente terminadoras — "si/no" van a GPT con contexto
    _listo = {"listo", "continuar", "correcto", "perfecto", "adelante",
              "proceder", "confirmar", "ok"}
    _listo_frases = ("todo bien", "está bien", "así está bien", "me parece bien",
                     "se ve bien", "todo ok", "así me gusta", "lo dejamos así")

    historial_cfg: List[Dict] = []

    while True:
        user_input = _ask("\n  Tú: ", "").strip()

        if not user_input:
            continue

        if user_input.lower() in _listo or any(kw in user_input.lower() for kw in _listo_frases):
            break

        if not puede_conversar:
            if HAS_RICH:
                _console.print("[yellow]  Sin OpenAI API key — escribe 'listo' para confirmar.[/yellow]")
            else:
                print("  Sin OpenAI API key — escribe 'listo' para confirmar.")
            continue

        try:
            if HAS_RICH:
                _console.print("[dim]  ...[/dim]")
            total, distribucion, escenarios_extra, mensaje, debe_confirmar = _ajustar_con_gpt(
                total, distribucion, escenarios_extra, user_input, openai_key,
                historial=historial_cfg,
            )
        except Exception as exc:
            if HAS_RICH:
                _console.print(f"[red]  Error: {exc}[/red]")
            else:
                print(f"  Error: {exc}")
            continue

        if HAS_RICH:
            _console.print(f"\n[green]  Juez:[/green] {mensaje}")
        else:
            print(f"\n  Juez: {mensaje}")

        # Guardar turno en historial (últimos 6)
        historial_cfg.append({
            "user": f"El usuario dice: {user_input}",
            "assistant": json.dumps({"accion": "confirmar" if debe_confirmar else "ajustar", "mensaje": mensaje}, ensure_ascii=False),
        })
        if len(historial_cfg) > 6:
            historial_cfg = historial_cfg[-6:]

        if debe_confirmar:
            break

        _mostrar_distribucion(total, distribucion, escenarios_extra)

        if HAS_RICH:
            _console.print("\n[dim]¿Algo más que ajustar?[/dim]")
        else:
            print("\n  ¿Algo más que ajustar?")

    concurrencia = min(max(total // 4, 2), 8)

    if HAS_RICH:
        _console.print(
            f"\n[green]  Configuración confirmada:[/green] "
            f"{total} conversaciones | concurrencia={concurrencia}"
        )
    else:
        print(f"\n  Configuración confirmada: {total} conversaciones | concurrencia={concurrencia}")

    return {
        "total": total,
        "distribucion": distribucion,
        "escenarios_extra": escenarios_extra,
        "concurrencia": concurrencia,
    }
