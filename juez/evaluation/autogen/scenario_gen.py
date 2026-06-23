"""Genera escenarios de evaluación con IA desde el contexto de negocio.

Entrada: sector, caso de uso, objetivo y medio (chat|llamada).
Salida: lista de escenarios para evaluar al agente.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

_SYSTEM = (
    "Eres un diseñador de pruebas de agentes conversacionales. Dado el contexto "
    "de negocio, generas escenarios de evaluación realistas y variados (happy path, "
    "casos límite, usuario molesto, fuera de alcance, adversarial). Respondes SOLO "
    "JSON: {\"escenarios\":[{\"titulo\":str,\"tipo\":str,\"descripcion\":str,"
    "\"entrada_usuario_ejemplo\":str,\"criterio_exito\":str}]}"
)


def _build_user(sector: str, caso_de_uso: str, objetivo: str, medio: str, n: int) -> str:
    return (
        f"Sector: {sector}\nCaso de uso: {caso_de_uso}\nObjetivo: {objetivo}\n"
        f"Medio: {medio} (adapta el tono: 'llamada' = voz, 'chat' = texto)\n"
        f"Genera {n} escenarios."
    )


def _parse(raw: str) -> List[Dict[str, Any]]:
    data = json.loads(raw or "{}")
    escenarios = data.get("escenarios", data if isinstance(data, list) else [])
    return [e for e in escenarios if isinstance(e, dict)]


def generate_scenarios(
    sector: str,
    caso_de_uso: str,
    objetivo: str,
    medio: str = "chat",
    n_escenarios: int = 8,
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> List[Dict[str, Any]]:
    """Llama al LLM y devuelve la lista de escenarios. Lanza RuntimeError sin API key."""
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta OPENAI_API_KEY para generar escenarios con IA")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user(sector, caso_de_uso, objetivo, medio, n_escenarios)},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    return _parse(resp.choices[0].message.content or "{}")


if __name__ == "__main__":  # ponytail: self-check sin red
    muestra = '{"escenarios":[{"titulo":"Consulta precio","tipo":"happy_path","descripcion":"x","entrada_usuario_ejemplo":"¿cuánto vale?","criterio_exito":"da el precio"}]}'
    assert _parse(muestra)[0]["tipo"] == "happy_path"
    assert _parse("{}") == []
    print("ok")
