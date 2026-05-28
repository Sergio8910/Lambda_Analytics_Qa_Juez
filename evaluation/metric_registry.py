from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from .report_models import EvaluationSpec, MetricSpec


MetricKind = Literal["llm", "rag", "safety", "extra", "deterministic"]


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    kind: MetricKind
    default_threshold: float
    requires_context: bool = False
    requires_expected_output: bool = False


METRICS: Dict[str, MetricDefinition] = {
    "answer_relevancy": MetricDefinition("answer_relevancy", "llm", 0.8),
    "instruction_adherence": MetricDefinition("instruction_adherence", "llm", 0.85, requires_expected_output=True),
    "task_success": MetricDefinition("task_success", "llm", 0.8, requires_expected_output=True),
    "faithfulness": MetricDefinition("faithfulness", "rag", 0.9, requires_context=True),
    "contextual_precision": MetricDefinition("contextual_precision", "rag", 0.8, requires_context=True),
    "hallucination": MetricDefinition("hallucination", "rag", 0.85, requires_context=True),
    "task_success_deterministic": MetricDefinition("task_success_deterministic", "deterministic", 0.67),
    "completeness": MetricDefinition("completeness", "deterministic", 0.8),
    "unsupported_claims": MetricDefinition("unsupported_claims", "deterministic", 0.7),
    "format_compliance": MetricDefinition("format_compliance", "deterministic", 1.0),
    "latency_budget": MetricDefinition("latency_budget", "deterministic", 1.0),
    "refusal_quality": MetricDefinition("refusal_quality", "deterministic", 0.8),
    "consistency": MetricDefinition("consistency", "deterministic", 0.8),
    "contract_clarification": MetricDefinition("contract_clarification", "deterministic", 1.0),
    "tool_call_validity": MetricDefinition("tool_call_validity", "extra", 1.0),
    "voice_quality": MetricDefinition("voice_quality", "deterministic", 0.8),
}

MetricRunner = Callable[[Any, Dict[str, Any], MetricSpec], Tuple[Any, Optional[Any]]]


def _run_answer_relevancy(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._run_llm_metric("answer_relevancy", metric, ctx), None


def _run_faithfulness(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._run_llm_metric("faithfulness", metric, ctx), None


def _run_contextual_precision(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._run_llm_metric("contextual_precision", metric, ctx), None


def _run_hallucination(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    from .metrics.contradiction_based_hallucination import ContradictionBasedHallucinationMetric

    metric_impl = ContradictionBasedHallucinationMetric()
    res = metric_impl.evaluate(
        output=ctx["output"],
        context=ctx["context"],
        threshold=metric.threshold,
        config=metric.config,
    )
    if res.score is None or metric.threshold is None:
        res.success = None
    else:
        res.success = res.score >= metric.threshold
    return res, None


def _run_instruction_adherence(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._run_instruction_adherence(metric, ctx), None


def _run_task_success(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._run_task_success(metric, ctx), None


def _run_task_success_deterministic(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return (
        engine._metric_task_success_deterministic(
            ctx["user_input"],
            ctx["tags"],
            ctx["output"],
            metric.threshold,
            ctx["context"],
            ctx.get("expected_behavior_raw", ""),
            ctx.get("expected_output_raw", ""),
        ),
        None,
    )


def _run_unsupported_claims(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._run_unsupported_claims(metric, ctx)


def _run_completeness(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return (
        engine._metric_completeness(
            ctx["user_input"], ctx["tags"], ctx["output"], metric.threshold, ctx["context"]
        ),
        None,
    )


def _run_format_compliance(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._metric_format_compliance(ctx["contract"], ctx["output"], metric.threshold), None


def _run_latency_budget(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._metric_latency_budget(ctx["latency_ms"], metric.threshold, metric.config), None


def _run_refusal_quality(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._metric_refusal_quality(ctx["user_input"], ctx["tags"], ctx["output"], metric.threshold), None


def _run_voice_quality(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return engine._heuristic_voice_quality(ctx["output"], metric.threshold), None


def _run_tool_call_validity(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return (
        engine._skip_metric_custom(
            "tool_call_validity",
            metric.threshold,
            reason="Métrica omitida: tool_calls no disponibles.",
            skip_reason="no_tool_calls",
        ),
        None,
    )


def _run_consistency(engine: Any, ctx: Dict[str, Any], metric: MetricSpec) -> Tuple[Any, Optional[Any]]:
    return (
        engine._skip_metric_custom(
            "consistency",
            metric.threshold,
            reason="Métrica omitida: no implementada en esta fase.",
            skip_reason="not_implemented",
        ),
        None,
    )


def _run_contract_clarification(
    engine: Any, ctx: Dict[str, Any], metric: MetricSpec
) -> Tuple[Any, Optional[Any]]:
    return (
        engine._metric_contract_clarification(
            ctx["user_input"], ctx["output"], ctx["contract"], metric.threshold
        ),
        None,
    )


METRIC_RUNNERS: Dict[str, MetricRunner] = {
    "answer_relevancy": _run_answer_relevancy,
    "instruction_adherence": _run_instruction_adherence,
    "task_success": _run_task_success,
    "task_success_deterministic": _run_task_success_deterministic,
    "completeness": _run_completeness,
    "faithfulness": _run_faithfulness,
    "contextual_precision": _run_contextual_precision,
    "hallucination": _run_hallucination,
    "unsupported_claims": _run_unsupported_claims,
    "format_compliance": _run_format_compliance,
    "latency_budget": _run_latency_budget,
    "refusal_quality": _run_refusal_quality,
    "consistency": _run_consistency,
    "contract_clarification": _run_contract_clarification,
    "tool_call_validity": _run_tool_call_validity,
    "voice_quality": _run_voice_quality,
}


def resolve_metric_specs(spec: EvaluationSpec) -> List[MetricSpec]:
    if spec.metrics:
        return spec.metrics
    names: List[str] = []
    for bucket in [spec.llm_metrics, spec.rag_metrics, spec.safety_metrics, spec.extra_metrics]:
        if bucket:
            names.extend([str(n).strip() for n in bucket])
    dedup = []
    for n in names:
        if n and n not in dedup:
            dedup.append(n)
    specs: List[MetricSpec] = []
    for name in dedup:
        definition = METRICS.get(name)
        if not definition:
            specs.append(
                MetricSpec(
                    name=name,
                    threshold=1.0,
                    enabled=True,
                    weight=1.0,
                    config={},
                )
            )
            continue
        specs.append(
            MetricSpec(
                name=name,
                threshold=definition.default_threshold,
                enabled=True,
                weight=1.0,
                config={},
            )
        )
    return specs
