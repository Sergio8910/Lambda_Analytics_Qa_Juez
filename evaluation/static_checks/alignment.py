"""Alineación tool ↔ prompt — chequeo estático.

Detecta dos clases de desalineación entre la lista de tools de un agente y
su system prompt:

  1. Tool conectada al agente que no se menciona en el prompt — el LLM no
     tiene guía explícita de cuándo invocarla, depende solo de la
     descripción de la tool.
  2. Identificador tipo nombre-de-tool en el prompt que no corresponde a
     ninguna tool real — posible referencia rota o renombrado pendiente.

Es genérico: opera sobre `(agent_name, prompt, tool_names)`. Cualquier
evaluador (n8n, ElevenLabs, ...) puede llamarlo después de normalizar su
input.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


# Identificadores tipo `Foo_Bar`, `foo_bar_baz` o `FooBarBaz` con al menos
# un separador (_) o dos mayúsculas internas. Patrón conservador para no
# confundir palabras comunes con nombres de tools.
_RE_TOOL_LIKE_IDENT = re.compile(
    r"\b(?:[A-Za-z][a-z]+(?:[A-Z][a-z]+){1,}|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b"
)


def variantes_nombre_tool(nombre: str) -> List[str]:
    """Variantes razonables de un nombre de tool para buscar en un prompt.

    Cubre los casos típicos en los que el prompt menciona la tool con un
    formato ligeramente diferente al canónico:

      - Mayúsculas/minúsculas distintas.
      - Sufijos numéricos del editor (n8n agrega `2/3/...` al duplicar).
      - Espacios en lugar de underscores.

    Filtra variantes muy cortas (<4 chars) para evitar matches espurios
    contra palabras comunes.
    """
    base = nombre.strip()
    sin_sufijo_digitos = re.sub(r"\d+$", "", base)
    variantes = {
        base,
        base.lower(),
        sin_sufijo_digitos,
        sin_sufijo_digitos.lower(),
        base.replace("_", " "),
        base.replace("_", " ").lower(),
        sin_sufijo_digitos.replace("_", " "),
        sin_sufijo_digitos.replace("_", " ").lower(),
    }
    return [v for v in variantes if len(v) >= 4]


def check_tool_prompt_alignment(
    agent_name: str,
    system_prompt: str,
    tool_names: Iterable[str],
) -> List[Dict[str, Any]]:
    """Devuelve una lista de problemas en el formato común del Juez.

    Si `system_prompt` está vacío o no hay tools, retorna lista vacía
    (la métrica no aplica).

    `tool_names` debe ser la lista de tools EFECTIVAMENTE conectadas al
    agente. En plataformas con un solo agente (ElevenLabs) es la lista
    completa de tools del config; en plataformas con varios agentes (n8n)
    es la lista de tools cuyo `agente_padre == agent_name`.
    """
    if not system_prompt or not tool_names:
        return []

    tool_list = [str(t).strip() for t in tool_names if t]
    if not tool_list:
        return []

    prompt_lower = system_prompt.lower()

    # Set normalizado de TODOS los nombres (con variantes) para el chequeo
    # de referencias fantasma — incluye también las tools que sí aparecen,
    # para no flagearlas como fantasma cuando se las cita con casing distinto.
    nombres_norm: set = set()
    for nombre in tool_list:
        for v in variantes_nombre_tool(nombre):
            nombres_norm.add(v.lower())

    problemas: List[Dict[str, Any]] = []

    # ── Check A: tools no mencionadas en el prompt ───────────────────────
    for nombre in tool_list:
        variantes = variantes_nombre_tool(nombre)
        if not any(v.lower() in prompt_lower for v in variantes):
            problemas.append({
                "tipo": "Alineacion Tools",
                "descripcion": (
                    f"Tool '{nombre}' conectada a '{agent_name}' no se menciona "
                    "en el system prompt — el LLM no tiene guía explícita de "
                    "cuándo invocarla."
                ),
                "nodo": nombre,
                "severidad": "MEDIO",
            })

    # ── Check B: identificadores en el prompt sin tool correspondiente ───
    for cand in set(_RE_TOOL_LIKE_IDENT.findall(system_prompt)):
        cand_lower = cand.lower()
        if cand_lower in nombres_norm:
            continue
        # Comparar también sin sufijo numérico para tolerar 'Registrar_Inmueble2'
        if re.sub(r"\d+$", "", cand).lower() in nombres_norm:
            continue
        # Reducir falsos positivos: solo flagear si tiene mayúscula o ≥2 underscores
        tiene_mayus = any(c.isupper() for c in cand)
        tiene_doble_us = cand.count("_") >= 2
        if not (tiene_mayus or tiene_doble_us):
            continue
        problemas.append({
            "tipo": "Alineacion Tools",
            "descripcion": (
                f"El prompt de '{agent_name}' menciona '{cand}' como si "
                "fuera una tool, pero no existe ninguna tool con ese nombre "
                "— posible referencia rota o nombre cambiado."
            ),
            "nodo": agent_name,
            "severidad": "BAJO",
        })

    return problemas
