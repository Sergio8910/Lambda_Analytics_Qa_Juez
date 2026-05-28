"""Autogeneración de casos y evaluación autónoma."""

from __future__ import annotations

from typing import List, Optional

from ..core.engine import EvaluationEngine
from ..report_models import EvaluationSpec, MetricSpec, RunReport, TestCase
from ..runner import run_agent
from .prompt_analyzer import analyze_prompt
from .case_generator import generate_cases
from .context_generator import generate_context_for_case
from .schemas import AutoGenSummary


def run_auto_eval(
    prompt_base: str,
    metrics: List[MetricSpec],
    n_cases: int,
    seed: Optional[int],
    run_id: str,
) -> RunReport:
    spec = EvaluationSpec(run_id=run_id, metrics=metrics, prompt_base=prompt_base)
    profile = analyze_prompt(prompt_base)
    cases = generate_cases(profile, n_cases=n_cases, seed=seed or spec.seed)

    context_counts = []
    context_nodes_by_case = {}
    for tc in cases:
        ctx_nodes = generate_context_for_case(profile, tc, seed=seed or spec.seed)
        tc.retrieval_context = [n["text"] for n in ctx_nodes]
        context_nodes_by_case[tc.case_id] = ctx_nodes
        context_counts.append(len(ctx_nodes))

    engine = EvaluationEngine(spec)
    report = engine.evaluate_run(cases, lambda tc: run_agent(spec, tc), dump_normalized_run=True)

    distribution = {}
    for tc in cases:
        for tag in tc.tags:
            distribution[tag] = distribution.get(tag, 0) + 1

    context_stats = {
        "min_chunks": min(context_counts) if context_counts else 0,
        "max_chunks": max(context_counts) if context_counts else 0,
        "avg_chunks": sum(context_counts) / max(len(context_counts), 1),
    }

    autogen_summary = AutoGenSummary(
        n_cases=len(cases),
        seed=seed or spec.seed,
        distribution_counts=distribution,
        context_stats=context_stats,
        failures_by_tag=report.summary.by_tag_failures,
        notes=[],
    )
    summary_dict = autogen_summary.model_dump(mode="json")
    summary_dict["context_nodes_by_case"] = context_nodes_by_case
    report.summary.autogen_summary = summary_dict
    return report
