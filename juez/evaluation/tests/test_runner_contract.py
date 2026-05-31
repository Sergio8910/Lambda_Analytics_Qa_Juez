from __future__ import annotations

import sys
import types

from juez.evaluation.report_models import EvaluationSpec
from juez.evaluation.runner import run_agent


def test_runner_normaliza_respuesta() -> None:
    mod = types.ModuleType("fake_agent_ok")

    def run_agent_ok(user_input: str):
        return {"response": "ok", "retrieval_context": ["ctx"]}  # type: ignore[return-value]

    mod.run_agent = run_agent_ok  # type: ignore[attr-defined]
    sys.modules["fake_agent_ok"] = mod
    spec = EvaluationSpec(run_id="t", agent_module="fake_agent_ok", agent_function="run_agent", metrics=[])
    result = run_agent(spec, "hola")
    assert result.output_text == "ok"
    assert result.retrieval_context == ["ctx"]


def test_runner_normaliza_response_key() -> None:
    mod = types.ModuleType("fake_agent_resp")

    def run_agent_ok(user_input: str):
        return {"response": "ok"}  # type: ignore[return-value]

    mod.run_agent = run_agent_ok  # type: ignore[attr-defined]
    sys.modules["fake_agent_resp"] = mod
    spec = EvaluationSpec(run_id="t", agent_module="fake_agent_resp", agent_function="run_agent", metrics=[])
    result = run_agent(spec, "hola")
    assert result.output_text == "ok"


def test_runner_detecta_formato_invalido() -> None:
    mod = types.ModuleType("fake_agent_bad")

    def run_agent_bad(user_input: str):
        return "texto"  # type: ignore[return-value]

    mod.run_agent = run_agent_bad  # type: ignore[attr-defined]
    sys.modules["fake_agent_bad"] = mod
    spec = EvaluationSpec(run_id="t", agent_module="fake_agent_bad", agent_function="run_agent", metrics=[])
    result = run_agent(spec, "hola")
    assert result.error is not None
