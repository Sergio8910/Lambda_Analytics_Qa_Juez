"""Tests del generador por lotes concurrentes (para volumenes grandes, ej.
500 conversaciones) -- NO toca OpenAI real, mockea el cliente para que cada
'lote' (llamada al LLM) devuelva un JSON valido de planes de forma
deterministica y sin gastar tokens.

Contexto: una sola llamada al LLM pidiendo cientos de conversaciones se
trunca en la practica y todo cae al respaldo heuristico (unas pocas
plantillas repetidas). El generador por lotes parte la distribucion en
tandas mas chicas, corridas EN PARALELO (varias 'obreras' generando a la
vez), acumulando los openers ya usados para mantener diversidad entre
tandas.
"""
from __future__ import annotations

import json
import threading

from juez.evaluation.contra_agente import generator
from juez.evaluation.contra_agente.generator import (
    _MAX_CONVERSACIONES_POR_LLAMADA,
    _build_generator_prompt,
    _dividir_distribucion_en_lotes,
    generar_batch,
)

_ANALISIS = {"agent_id": "agente_test", "prompt": {"completo": "Eres un agente de prueba."}, "tools": []}


def _plan_json(plan_id: str, category: str, opener: str) -> dict:
    return {
        "plan_id": plan_id,
        "category": category,
        "severity": "media",
        "tags": [category],
        "success_threshold": 0.70,
        "max_turns": 1,
        "persona": {"name": "Usuario", "mood": "cordial", "backstory": "", "language_style": "informal"},
        "turns": [
            {
                "turn_id": 1, "turn_type": "opener", "intent": "probar",
                "message_template": opener, "fragmentos": None, "fragmento_delay_ms": 800,
                "success_criteria": "el agente responde", "metrics": ["task_success"],
                "adaptive_logic": None, "variables": {},
            }
        ],
        "notes": None,
    }


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, contador: dict, lock: threading.Lock, capturar_prompts: list) -> None:
        self._contador = contador
        self._lock = lock
        self._capturar_prompts = capturar_prompts

    def create(self, *, model, messages, temperature, response_format):
        prompt_usuario = messages[-1]["content"]
        with self._lock:
            self._contador["llamadas"] += 1
            idx = self._contador["llamadas"]
            self._capturar_prompts.append(prompt_usuario)
        # Devuelve tantos planes como pida la distribucion del prompt (aproximado:
        # basta con devolver un numero fijo razonable por lote para el test).
        plans = [_plan_json(f"conv_{idx}_{i}", "happy_path", f"Opener lote {idx} numero {i}") for i in range(5)]
        return _FakeResponse(json.dumps({"plans": plans}))


class _FakeOpenAI:
    _contador = {"llamadas": 0}
    _lock = threading.Lock()
    prompts_capturados: list = []

    def __init__(self, api_key: str) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self._contador, self._lock, self.prompts_capturados)})()


def test_dividir_distribucion_en_lotes_no_pierde_conversaciones():
    distribucion = {"happy_path": 25, "herramienta": 15, "seguridad": 5}
    lotes = _dividir_distribucion_en_lotes(distribucion, tamano_lote=20)
    total_en_lotes = sum(sum(lote.values()) for lote in lotes)
    assert total_en_lotes == sum(distribucion.values())
    assert all(sum(lote.values()) <= 20 for lote in lotes)


def test_dividir_distribucion_respeta_tamano_maximo():
    distribucion = {"happy_path": 100}
    lotes = _dividir_distribucion_en_lotes(distribucion, tamano_lote=20)
    assert len(lotes) == 5
    assert all(sum(lote.values()) == 20 for lote in lotes)


def test_batch_grande_usa_generacion_por_lotes_en_paralelo(monkeypatch):
    import openai as openai_mod

    contador = {"llamadas": 0}
    fake = _FakeOpenAI
    fake._contador = {"llamadas": 0}
    fake.prompts_capturados = []
    monkeypatch.setattr(openai_mod, "OpenAI", fake)

    total = 60  # > _MAX_CONVERSACIONES_POR_LLAMADA -> debe ir por el camino de lotes
    batch = generar_batch(_ANALISIS, "Agente Test", total=total, openai_key="fake-key")

    assert total > _MAX_CONVERSACIONES_POR_LLAMADA
    # Con tamano_lote=20 y total=60 -> 3 lotes -> 3 llamadas al LLM (menos que 60 llamadas individuales)
    assert fake._contador["llamadas"] == 3
    # plan_ids re-numerados globalmente sin colisiones
    ids = [p.plan_id for p in batch.plans]
    assert len(ids) == len(set(ids))


def test_batch_chico_sigue_usando_una_sola_llamada(monkeypatch):
    """Regresion: para totales pequenos (comportamiento existente), debe seguir
    siendo UNA sola llamada al LLM, sin pasar por el camino de lotes."""
    import openai as openai_mod

    fake = _FakeOpenAI
    fake._contador = {"llamadas": 0}
    fake.prompts_capturados = []
    monkeypatch.setattr(openai_mod, "OpenAI", fake)

    total = 10  # <= _MAX_CONVERSACIONES_POR_LLAMADA
    generar_batch(_ANALISIS, "Agente Test", total=total, openai_key="fake-key")
    assert fake._contador["llamadas"] == 1


def test_openers_previos_se_inyectan_en_el_prompt_de_tandas_siguientes(monkeypatch):
    import openai as openai_mod

    fake = _FakeOpenAI
    fake._contador = {"llamadas": 0}
    fake.prompts_capturados = []
    monkeypatch.setattr(openai_mod, "OpenAI", fake)

    total = 60
    generar_batch(_ANALISIS, "Agente Test", total=total, openai_key="fake-key")

    # Con _LOTES_CONCURRENTES_POR_TANDA=5 y solo 3 lotes, todos van en UNA tanda,
    # asi que ningun lote ve openers de otro (todos arrancan sin contexto previo).
    # Forzamos un caso con mas lotes que el tamano de tanda para probar la
    # propagacion de openers entre tandas.
    fake._contador = {"llamadas": 0}
    fake.prompts_capturados = []
    total_grande = 20 * 8  # 8 lotes -> 2 tandas con _LOTES_CONCURRENTES_POR_TANDA=5
    generar_batch(_ANALISIS, "Agente Test", total=total_grande, openai_key="fake-key")
    # Los prompts de la SEGUNDA tanda deben mencionar los openers ya usados.
    ultimos_prompts = fake.prompts_capturados[5:]
    assert any("OPENERS YA USADOS" in p for p in ultimos_prompts)


def test_prompt_incluye_seccion_de_openers_previos_si_se_pasan():
    prompt_sin = _build_generator_prompt(_ANALISIS, "Agente Test", {"happy_path": 1})
    prompt_con = _build_generator_prompt(
        _ANALISIS, "Agente Test", {"happy_path": 1}, openers_previos=["Hola, tengo una consulta"],
    )
    assert "OPENERS YA USADOS" not in prompt_sin
    assert "OPENERS YA USADOS" in prompt_con
    assert "Hola, tengo una consulta" in prompt_con
