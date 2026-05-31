from juez.evaluation.utils.text_normalization import repair_text
from juez.evaluation.judge_engine import JudgeEngine
from juez.evaluation.metric_registry import METRIC_RUNNERS
from juez.evaluation.report_models import EvaluationSpec, MetricSpec, TaskContract, TestCase
from juez.evaluation.adapters.default_callable import DefaultCallableAdapter


def test_encoding_repair():
    assert repair_text("cafÃ©") == "café"


def test_hallucination_polarity():
    spec = EvaluationSpec(run_id="t", metrics=[])
    engine = JudgeEngine(spec)
    result = engine._build_metric_from_worker_result(
        "hallucination", 1.0, {"score": 1.0, "reason": "ok", "success": True}
    )
    assert result.score == 1.0
    assert result.success is True


def test_contextual_precision_skip():
    spec = EvaluationSpec(run_id="t", metrics=[])
    engine = JudgeEngine(spec)
    res = engine._heuristic_contextual_precision(
        output="No tengo acceso a esa información.", context=["Producto X $1.00"], threshold=0.8
    )
    assert res.skipped is True
    assert res.skip_reason == "no_context_used"


def test_contract_clarification():
    # El input "¿Qué me recomiendas?" solo es "ambiguo" si hay vocabulario de
    # dominio que lo defina como tal. Antes esto estaba hardcodeado en el
    # engine; ahora se declara vía spec.domain_vocabulary_id.
    spec = EvaluationSpec(run_id="t", metrics=[], domain_vocabulary_id="supermercado")
    engine = JudgeEngine(spec)
    contract = TaskContract(require_clarifying_question_if_ambiguous=True)
    res = engine._metric_contract_clarification(
        "¿Qué me recomiendas?", "Tengo varias opciones.", contract, 1.0
    )
    assert res.score == 0.0
    assert res.success is False


def test_contract_clarification_skipped_without_vocab():
    # Sin vocabulario de dominio el motor no puede juzgar ambigüedad —
    # debe omitir la métrica en lugar de devolver datos sesgados.
    spec = EvaluationSpec(run_id="t", metrics=[])
    engine = JudgeEngine(spec)
    contract = TaskContract(require_clarifying_question_if_ambiguous=True)
    res = engine._metric_contract_clarification(
        "¿Qué me recomiendas?", "Tengo varias opciones.", contract, 1.0
    )
    assert res.skipped is True
    assert res.skip_reason == "no_domain_vocab"


def test_consistency_not_silent():
    spec = EvaluationSpec(run_id="t", metrics=[MetricSpec(name="consistency", threshold=0.8)])
    engine = JudgeEngine(spec)
    runner = METRIC_RUNNERS.get("consistency")
    assert runner is not None
    res, _ = runner(engine, {"user_input": "", "output": "", "contract": TaskContract()}, spec.metrics[0])
    assert res.name == "consistency"
    assert res.skipped is True
    assert res.skip_reason == "not_implemented"


def test_agent_kind_callable_normalized():
    spec = EvaluationSpec(run_id="t", agent_kind="callable")
    adapter = DefaultCallableAdapter()
    case = TestCase(case_id="X1", input="hola", tags=[], severity="baja")
    normalized = adapter.build_normalized_run(
        case, {"response": "ok", "retrieval_context": ["ctx"]}, spec
    )
    assert normalized.agent.kind == "chat"
