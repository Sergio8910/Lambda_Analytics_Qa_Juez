from evaluation.utils.text_normalization import repair_text, repair_recursive
from evaluation.report_models import EvaluationSpec, MetricSpec, TestCase
from evaluation.core.engine import EvaluationEngine


def test_repair_text_basic():
    assert repair_text("cafÃ©") == "café"


def test_repair_recursive_nested():
    data = {"a": "MÃ©trica", "b": ["cafÃ©", {"c": "lÃ¡cteo"}]}
    fixed = repair_recursive(data)
    assert fixed["a"] == "Métrica"
    assert fixed["b"][0] == "café"
    assert fixed["b"][1]["c"] == "lácteo"


def test_report_no_mojibake():
    spec = EvaluationSpec(
        run_id="t",
        agent_module="fake",
        agent_function="run_agent",
        metrics=[MetricSpec(name="format_compliance", threshold=1.0)],
        enable_metamorphic=False,
    )
    engine = EvaluationEngine(spec)
    case = TestCase(
        case_id="C1",
        input="Precio del café",
        tags=[],
        severity="baja",
        expected_behavior="Responder precio",
        context=["Café $1.00"],
    )
    report = engine.evaluate_run([case], lambda x: type("R", (), {"output_text": "cafÃ© $1.00", "retrieval_context": [], "latency_ms": 1.0})())
    data = report.model_dump(mode="json")
    from evaluation.utils.text_normalization import repair_recursive as _rr

    clean = _rr(data)
    import json

    json_out = json.dumps(clean, ensure_ascii=False)
    assert "Ã" not in json_out
