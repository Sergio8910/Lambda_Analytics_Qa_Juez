from __future__ import annotations

import sys
import types

from juez.evaluation.judge_engine import JudgeEngine
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, TestCase
from juez.evaluation.runner import run_agent


def test_end_to_end_smoke() -> None:
    mod = types.ModuleType("fake_agent_e2e")

    def run_agent_ok(user_input: str):
        return {"response": "La leche cuesta $1.29", "retrieval_context": ["Lacteos: leche entera 1L $1.29."]}  # type: ignore[return-value]

    mod.run_agent = run_agent_ok  # type: ignore[attr-defined]
    sys.modules["fake_agent_e2e"] = mod

    spec = EvaluationSpec(
        run_id="e2e",
        agent_module="fake_agent_e2e",
        agent_function="run_agent",
        metrics=[
            MetricSpec(name="unsupported_claims", threshold=0.5, enabled=True, weight=1.0, config={}),
            MetricSpec(name="format_compliance", threshold=1.0, enabled=True, weight=1.0, config={}),
        ],
        enable_metamorphic=False,
    )
    engine = JudgeEngine(spec)
    case = TestCase(
        case_id="C1",
        input="¿Cuánto cuesta la leche?",
        tags=["smoke"],
        severity="baja",
        expected_behavior="Responder el precio de la leche.",
        context=["Lacteos: leche entera 1L $1.29."],
    )
    report = engine.evaluate_run([case], lambda x: run_agent(spec, x))
    assert report.summary.total_cases == 1
