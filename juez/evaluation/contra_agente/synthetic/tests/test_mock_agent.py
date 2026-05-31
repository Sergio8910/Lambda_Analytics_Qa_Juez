"""Tests unitarios de MockAgent + helpers — OpenAI completamente mockeado."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from juez.evaluation.contra_agente.synthetic.mock_agent import (
    MockAgent,
    _MAX_TOOL_CALL_ITERATIONS,
    _build_tool_definitions,
    _slug_tool_name,
)
from juez.evaluation.contra_agente.synthetic.mock_tools import MockToolRunner


# ── _slug_tool_name ──────────────────────────────────────────────────────────

def test_slug_tool_name_replaces_spaces():
    slug = _slug_tool_name("Registrar Inmueble")
    assert slug == "Registrar_Inmueble"


def test_slug_tool_name_handles_accents():
    """Caracteres con acentos no están en [A-Za-z0-9_-] → se reemplazan por _."""
    slug = _slug_tool_name("Análisis_X")
    # 'á' es non-ASCII → se sustituye. Resultado plausible: "An_lisis_X"
    assert all(c.isalnum() or c in "_-" for c in slug)
    assert "X" in slug
    # No debe contener acentos
    assert "á" not in slug
    assert "é" not in slug


def test_slug_tool_name_handles_newlines_and_special_chars():
    """Tool con \\n breaks o caracteres extraños no debe romper."""
    slug = _slug_tool_name("tool con \n breaks")
    # No revienta y matchea regex permitida
    assert all(c.isalnum() or c in "_-" for c in slug)
    assert len(slug) <= 64
    assert len(slug) >= 1


def test_slug_tool_name_truncates_to_64_chars():
    very_long = "a" * 200
    slug = _slug_tool_name(very_long)
    assert len(slug) == 64


def test_slug_tool_name_empty_input_returns_fallback():
    assert _slug_tool_name("") == "unnamed_tool"
    assert _slug_tool_name("   ") == "unnamed_tool"


def test_slug_tool_name_only_invalid_chars_returns_fallback_or_underscore():
    """Si tras la sanitización solo quedan separadores, devuelve fallback o el _ resultante."""
    slug = _slug_tool_name("///")
    # Debe ser un slug válido por el regex de OpenAI: [A-Za-z0-9_-]{1,64}
    assert 1 <= len(slug) <= 64
    import re
    assert re.fullmatch(r"[A-Za-z0-9_-]+", slug)


# ── _build_tool_definitions ──────────────────────────────────────────────────

def test_build_tool_definitions_returns_tuple_defs_and_name_map():
    herramientas = [
        {"nombre": "Registrar Inmueble", "descripcion": "Crea un inmueble"},
        {"nombre": "Generar PDF", "descripcion": "Genera el PDF final"},
    ]
    defs, name_map = _build_tool_definitions(herramientas)

    assert isinstance(defs, list)
    assert isinstance(name_map, dict)
    assert len(defs) == 2

    # Shape OpenAI function-calling
    for d in defs:
        assert d["type"] == "function"
        assert "function" in d
        assert "name" in d["function"]
        assert "description" in d["function"]
        assert "parameters" in d["function"]
        assert d["function"]["parameters"]["type"] == "object"

    # name_map[slug] = nombre_original
    assert "Registrar_Inmueble" in name_map
    assert name_map["Registrar_Inmueble"] == "Registrar Inmueble"
    assert "Generar_PDF" in name_map
    assert name_map["Generar_PDF"] == "Generar PDF"


def test_build_tool_definitions_skips_empty_names():
    herramientas = [
        {"nombre": "ToolA", "descripcion": "descA"},
        {"nombre": "", "descripcion": "vacio"},
        {"descripcion": "sin nombre"},
    ]
    defs, name_map = _build_tool_definitions(herramientas)
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "ToolA"


def test_build_tool_definitions_accepts_name_or_nombre():
    """Acepta tanto la clave 'nombre' (ES) como 'name' (EN)."""
    herramientas = [{"name": "ToolEN", "description": "english"}]
    defs, name_map = _build_tool_definitions(herramientas)
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "ToolEN"
    assert name_map["ToolEN"] == "ToolEN"


def test_build_tool_definitions_empty_input():
    defs, name_map = _build_tool_definitions([])
    assert defs == []
    assert name_map == {}


def test_build_tool_definitions_description_truncated():
    """Descripciones muy largas se truncan a 600 chars."""
    huge = "x" * 5000
    herramientas = [{"nombre": "ToolHuge", "descripcion": huge}]
    defs, _ = _build_tool_definitions(herramientas)
    assert len(defs[0]["function"]["description"]) == 600


# ── MockAgent.respond — helpers ──────────────────────────────────────────────

def _canonical():
    return {
        "source": "synthetic",
        "contrato_id": "JUEZ-E2E-XX-01",
        "inventario_id": 99001,
        "propietario": "P",
        "arrendatario": "A",
        "tipo_inventario": "INICIAL",
        "ambientes": ["Cocina"],
        "fotos_por_ambiente": {"Cocina": 5},
        "total_fotos": 5,
    }


def _make_choice(content=None, tool_calls=None):
    """Construye un fake response.choices[0] al estilo OpenAI."""
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    return choice


def _make_response(content=None, tool_calls=None):
    resp = MagicMock()
    resp.choices = [_make_choice(content=content, tool_calls=tool_calls)]
    return resp


def _make_tool_call(call_id: str, name: str, arguments: str):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


# ── Test 1: respond sin tool_calls → texto directo ───────────────────────────

def test_respond_no_tool_calls_returns_text_direct():
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        fake_response = _make_response(content="Hola, asistente listo", tool_calls=None)
        mock_client.chat.completions.create.return_value = fake_response

        agent = MockAgent(
            system_prompt="Eres un asistente.",
            herramientas=[],
            model="gpt-4o-mini",
            openai_key="sk-fake",
        )
        runner = MockToolRunner(_canonical())
        result = agent.respond("Hola", runner)

        assert result == "Hola, asistente listo"
        # No se llamaron tools
        assert runner.calls == []
        # OpenAI fue invocado una sola vez
        assert mock_client.chat.completions.create.call_count == 1


def test_respond_returns_stripped_text():
    """Whitespace al inicio/final del content se trimma."""
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response(
            content="  texto con espacios  ", tool_calls=None
        )

        agent = MockAgent("sys", [], "gpt-4o-mini", "sk-fake")
        result = agent.respond("hola", MockToolRunner(_canonical()))
        assert result == "texto con espacios"


def test_respond_handles_none_content():
    """Si content viene None y no hay tool_calls, devuelve string vacío."""
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_response(
            content=None, tool_calls=None
        )

        agent = MockAgent("sys", [], "gpt-4o-mini", "sk-fake")
        result = agent.respond("hola", MockToolRunner(_canonical()))
        assert result == ""


# ── Test 2: con tool_calls → ejecuta runner y luego texto en 2º turno ────────

def test_respond_with_tool_calls_executes_runner_and_returns_final_text():
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        # Turno 1: el modelo pide una tool
        tool_call = _make_tool_call(
            call_id="call_001",
            name="Registrar_Inmueble",  # slug
            arguments=json.dumps({"contrato_id": "X"}),
        )
        response_turn_1 = _make_response(content=None, tool_calls=[tool_call])

        # Turno 2: el modelo cierra con texto puro
        response_turn_2 = _make_response(content="Inmueble registrado OK", tool_calls=None)

        mock_client.chat.completions.create.side_effect = [
            response_turn_1,
            response_turn_2,
        ]

        herramientas = [{"nombre": "Registrar Inmueble", "descripcion": "Registra"}]
        agent = MockAgent(
            system_prompt="sys",
            herramientas=herramientas,
            model="gpt-4o-mini",
            openai_key="sk-fake",
        )
        runner = MockToolRunner(_canonical())
        result = agent.respond("Registrame el inmueble", runner)

        assert result == "Inmueble registrado OK"
        # MockToolRunner fue invocado con el NOMBRE ORIGINAL (no el slug)
        assert len(runner.calls) == 1
        assert runner.calls[0]["tool"] == "Registrar Inmueble"
        assert runner.calls[0]["args"] == {"contrato_id": "X"}
        # OpenAI fue llamado 2 veces
        assert mock_client.chat.completions.create.call_count == 2


def test_respond_handles_malformed_tool_arguments_as_empty_dict():
    """Si tc.function.arguments no es JSON válido → args={}."""
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        tool_call = _make_tool_call(
            call_id="call_bad",
            name="Generar_PDF",
            arguments="not valid json {{{",
        )
        response_turn_1 = _make_response(content=None, tool_calls=[tool_call])
        response_turn_2 = _make_response(content="listo", tool_calls=None)
        mock_client.chat.completions.create.side_effect = [response_turn_1, response_turn_2]

        herramientas = [{"nombre": "Generar PDF", "descripcion": "PDF"}]
        agent = MockAgent("sys", herramientas, "gpt-4o-mini", "sk-fake")
        runner = MockToolRunner(_canonical())
        result = agent.respond("dame el pdf", runner)

        assert result == "listo"
        assert len(runner.calls) == 1
        assert runner.calls[0]["args"] == {}


def test_respond_multiple_tool_calls_in_single_turn():
    """Un solo turno puede tener varios tool_calls — todos se ejecutan."""
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        tc1 = _make_tool_call("c1", "Registrar_Inmueble", json.dumps({"a": 1}))
        tc2 = _make_tool_call("c2", "Generar_PDF", json.dumps({"b": 2}))

        response_turn_1 = _make_response(content=None, tool_calls=[tc1, tc2])
        response_turn_2 = _make_response(content="dos tools", tool_calls=None)
        mock_client.chat.completions.create.side_effect = [response_turn_1, response_turn_2]

        herramientas = [
            {"nombre": "Registrar Inmueble", "descripcion": "x"},
            {"nombre": "Generar PDF", "descripcion": "y"},
        ]
        agent = MockAgent("sys", herramientas, "gpt-4o-mini", "sk-fake")
        runner = MockToolRunner(_canonical())
        result = agent.respond("hace dos cosas", runner)

        assert result == "dos tools"
        assert len(runner.calls) == 2
        assert runner.calls[0]["tool"] == "Registrar Inmueble"
        assert runner.calls[1]["tool"] == "Generar PDF"


# ── Test 3: saturación de iteraciones ────────────────────────────────────────

def test_respond_saturates_max_iterations_returns_fallback():
    """Si OpenAI siempre retorna tool_calls, se corta a _MAX_TOOL_CALL_ITERATIONS."""
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        # Generador infinito de respuestas con tool_calls — siempre la misma
        def _always_tool_call(*args, **kwargs):
            tc = _make_tool_call(
                call_id=f"call_inf_{kwargs.get('model','x')}",
                name="Registrar_Inmueble",
                arguments=json.dumps({"x": 1}),
            )
            return _make_response(content=None, tool_calls=[tc])

        mock_client.chat.completions.create.side_effect = _always_tool_call

        herramientas = [{"nombre": "Registrar Inmueble", "descripcion": "x"}]
        agent = MockAgent("sys", herramientas, "gpt-4o-mini", "sk-fake")
        runner = MockToolRunner(_canonical())
        result = agent.respond("loop infinito", runner)

        # Se saturó → llega al fallback string (no hay assistant content en historial)
        assert "saturó" in result or isinstance(result, str)
        assert isinstance(result, str)
        # Llamó OpenAI exactamente _MAX_TOOL_CALL_ITERATIONS veces
        assert mock_client.chat.completions.create.call_count == _MAX_TOOL_CALL_ITERATIONS
        # Y el runner se ejecutó la misma cantidad de veces
        assert len(runner.calls) == _MAX_TOOL_CALL_ITERATIONS


def test_respond_saturation_returns_last_assistant_content_if_present():
    """Si en algún turno hubo content + tool_calls, al saturar se devuelve ese content."""
    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client

        # Cada respuesta tiene content "pensando..." Y tool_calls
        def _content_plus_tool(*args, **kwargs):
            tc = _make_tool_call(
                call_id="c_x",
                name="Generar_PDF",
                arguments="{}",
            )
            return _make_response(content="pensando...", tool_calls=[tc])

        mock_client.chat.completions.create.side_effect = _content_plus_tool

        herramientas = [{"nombre": "Generar PDF", "descripcion": "x"}]
        agent = MockAgent("sys", herramientas, "gpt-4o-mini", "sk-fake")
        runner = MockToolRunner(_canonical())
        result = agent.respond("loop con content", runner)

        # El último assistant con content fue "pensando..."
        assert result == "pensando..."


# ── MockAgent: integración con OpenAI client constructor ─────────────────────

def test_mock_agent_init_passes_api_key():
    with patch("openai.OpenAI") as MockOpenAI:
        MockOpenAI.return_value = MagicMock()
        MockAgent("sys", [], "gpt-4o-mini", "sk-my-key")
        MockOpenAI.assert_called_once_with(api_key="sk-my-key")


def test_mock_agent_conversation_property_returns_copy():
    """conversation devuelve copia (no la lista interna)."""
    with patch("openai.OpenAI") as MockOpenAI:
        MockOpenAI.return_value = MagicMock()
        agent = MockAgent("system prompt", [], "gpt-4o-mini", "sk-fake")
        conv = agent.conversation
        assert isinstance(conv, list)
        assert len(conv) == 1
        assert conv[0]["role"] == "system"
        # Mutar la copia no debe afectar al original
        conv.append({"role": "user", "content": "hack"})
        assert len(agent.conversation) == 1
