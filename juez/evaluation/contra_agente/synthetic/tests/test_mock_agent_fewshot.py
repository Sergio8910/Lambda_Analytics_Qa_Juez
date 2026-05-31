"""Tests de la enriquecimiento few-shot del system prompt del MockAgent.

Cubre `_enriquecer_system_prompt()` y su integración con `MockAgent.__init__()`:
  - El prompt original queda íntegro como substring.
  - Se agregan las 3 secciones (Comportamiento esperado / Estilo / Ejemplos).
  - 0 tools no crashea y devuelve estructura válida.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from juez.evaluation.contra_agente.synthetic.mock_agent import (
    MockAgent,
    _enriquecer_system_prompt,
)


# ── _enriquecer_system_prompt: contenido base ────────────────────────────────

def test_enriquecer_preserva_prompt_original_como_substring():
    base = "Eres el asistente de inventarios de la inmobiliaria ACME. Sé conciso."
    herramientas = [
        {"nombre": "Registrar_Inmueble", "descripcion": "Registra el inmueble"},
    ]
    enriched = _enriquecer_system_prompt(base, herramientas)
    assert base in enriched, "El prompt original debe seguir presente íntegramente"


def test_enriquecer_contiene_seccion_comportamiento_esperado():
    enriched = _enriquecer_system_prompt(
        "system base",
        [{"nombre": "ToolA", "descripcion": "descA"}],
    )
    assert "## Comportamiento esperado por las tools disponibles" in enriched
    # Debe listar la tool con su descripción
    assert "ToolA" in enriched
    assert "descA" in enriched


def test_enriquecer_contiene_seccion_estilo():
    enriched = _enriquecer_system_prompt(
        "system base",
        [{"nombre": "ToolA", "descripcion": "descA"}],
    )
    assert "## Estilo de respuesta" in enriched
    # Las 3 instrucciones clave deben estar presentes (palabras pivote)
    lower = enriched.lower()
    assert "confirma" in lower
    # Disciplina de pedir datos faltantes (alguna de las raíces o palabras clave)
    assert "falta" in lower or "pregún" in lower or "aclar" in lower
    # Disciplina de no inventar
    assert "invent" in lower


def test_enriquecer_contiene_seccion_ejemplos():
    enriched = _enriquecer_system_prompt(
        "system base",
        [{"nombre": "ToolA", "descripcion": "descA"}],
    )
    assert "## Ejemplos" in enriched
    # Los dos ejemplos genéricos
    assert "Ejemplo 1" in enriched
    assert "Ejemplo 2" in enriched


# ── _enriquecer_system_prompt: edge cases ────────────────────────────────────

def test_enriquecer_con_cero_tools_no_crashea():
    """0 tools → no crashea, mantiene estructura, base intacto."""
    enriched = _enriquecer_system_prompt("solo base", [])
    assert "solo base" in enriched
    assert "## Comportamiento esperado por las tools disponibles" in enriched
    assert "## Estilo de respuesta" in enriched
    assert "## Ejemplos" in enriched


def test_enriquecer_con_tools_none_no_crashea():
    """tools=None se trata como lista vacía."""
    enriched = _enriquecer_system_prompt("base", None)  # type: ignore[arg-type]
    assert "base" in enriched
    assert "## Ejemplos" in enriched


def test_enriquecer_con_prompt_vacio_no_crashea():
    """base_prompt vacío sigue produciendo secciones."""
    enriched = _enriquecer_system_prompt("", [{"nombre": "T", "descripcion": "d"}])
    assert "## Comportamiento esperado por las tools disponibles" in enriched
    assert "## Estilo de respuesta" in enriched
    assert "## Ejemplos" in enriched


def test_enriquecer_descripcion_truncada_a_200_chars():
    """Descripciones largas se truncan a 200 chars en la línea de la tool."""
    huge_desc = "x" * 1000
    enriched = _enriquecer_system_prompt(
        "base",
        [{"nombre": "ToolHuge", "descripcion": huge_desc}],
    )
    # Buscamos la línea de la tool
    linea = next(
        (l for l in enriched.splitlines() if l.startswith("- ToolHuge")),
        None,
    )
    assert linea is not None
    # "- ToolHuge: " + 200 'x' = 212 chars
    assert linea.count("x") == 200


def test_enriquecer_acepta_clave_name_y_description_en_ingles():
    """Acepta tanto 'nombre'/'descripcion' (ES) como 'name'/'description' (EN)."""
    enriched = _enriquecer_system_prompt(
        "base",
        [{"name": "ToolEN", "description": "english desc"}],
    )
    assert "ToolEN" in enriched
    assert "english desc" in enriched


def test_enriquecer_omite_tools_sin_nombre():
    """Tools con nombre vacío o ausente se omiten silenciosamente."""
    enriched = _enriquecer_system_prompt(
        "base",
        [
            {"nombre": "ValidTool", "descripcion": "ok"},
            {"nombre": "", "descripcion": "vacio"},
            {"descripcion": "sin nombre"},
        ],
    )
    assert "ValidTool" in enriched
    # No debe aparecer una línea "- :" del tool vacío
    for linea in enriched.splitlines():
        if linea.startswith("- "):
            # Toda línea de tool debe tener un nombre no vacío antes del ':'
            assert linea[2:].split(":", 1)[0].strip() != ""


# ── Integración con MockAgent.__init__ ───────────────────────────────────────

def test_mock_agent_init_usa_prompt_enriquecido():
    """El system prompt almacenado por el agente es el enriquecido, no el crudo."""
    with patch("openai.OpenAI") as MockOpenAI:
        MockOpenAI.return_value = MagicMock()
        base = "Eres el asistente de Lambda."
        herramientas = [{"nombre": "Registrar_Inmueble", "descripcion": "registra"}]
        agent = MockAgent(base, herramientas, "gpt-4o-mini", "sk-fake")

        conv = agent.conversation
        assert len(conv) == 1
        assert conv[0]["role"] == "system"
        system_content = conv[0]["content"]

        # Prompt original presente
        assert base in system_content
        # Secciones del enriquecimiento presentes
        assert "## Comportamiento esperado por las tools disponibles" in system_content
        assert "## Estilo de respuesta" in system_content
        assert "## Ejemplos" in system_content
        # Tool listada
        assert "Registrar_Inmueble" in system_content


def test_mock_agent_init_con_cero_tools_no_crashea():
    """MockAgent con `herramientas=[]` arranca sin errores y deja el prompt enriquecido."""
    with patch("openai.OpenAI") as MockOpenAI:
        MockOpenAI.return_value = MagicMock()
        agent = MockAgent("base sin tools", [], "gpt-4o-mini", "sk-fake")
        conv = agent.conversation
        assert conv[0]["role"] == "system"
        assert "base sin tools" in conv[0]["content"]
        assert "## Ejemplos" in conv[0]["content"]
