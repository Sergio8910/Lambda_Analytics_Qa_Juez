"""Pool del contra-agente — ejecuta N conversaciones en paralelo con semáforo.

Usa ThreadPoolExecutor porque los adapters son síncronos (HTTP).
"""
from __future__ import annotations

import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .evaluator import TurnEvaluator
from .models import (
    BatchResult,
    ConversationBatch,
    ConversationPlan,
    ConversationResult,
)
from .synthetic.adapter import MockAdapter
from .synthetic.cost_meter import CostMeter
from .synthetic.mock_agent import MockAgent
from .synthetic.mock_tools import MockToolRunner
from .synthetic.pdf_builder import build_synthetic_pdf
from .verificador_client import VerificadorUnavailable, verify_inline_pdf
from .worker import ConversationWorker


def _error_result(plan: ConversationPlan, error_msg: str) -> ConversationResult:
    """Crea un ConversationResult de fallo técnico."""
    return ConversationResult(
        plan_id=plan.plan_id,
        category=plan.category,
        tags=plan.tags,
        passed=False,
        turn_results=[],
        collapse_turn=1,
        overall_score=0.0,
        transcript=[],
        latency_total_ms=0.0,
        diagnosis=f"Error técnico: {error_msg[:200]}",
    )


def _run_single_conversation(
    plan: ConversationPlan,
    adapter,
    evaluator: TurnEvaluator,
    openai_key: str = "",
    synthetic_context: Optional[Dict[str, Any]] = None,
    cost_meter: Optional[CostMeter] = None,
) -> ConversationResult:
    """Ejecuta una conversación individual. Thread-safe."""
    try:
        tool_runner: Optional[MockToolRunner] = None
        if plan.artifact_expectation is not None:
            if not openai_key:
                raise RuntimeError("Modo e2e requiere OPENAI_API_KEY para MockAgent.")
            synthetic_context = synthetic_context or {}
            tool_runner = MockToolRunner(plan.artifact_expectation.canonical_data)
            agent = MockAgent(
                system_prompt=str(synthetic_context.get("system_prompt") or ""),
                herramientas=list(synthetic_context.get("herramientas") or []),
                model=str(synthetic_context.get("model") or "gpt-4o-mini"),
                openai_key=openai_key,
                cost_meter=cost_meter,
            )
            adapter = MockAdapter(agent=agent, tool_runner=tool_runner)

        worker = ConversationWorker(
            plan=plan,
            adapter=adapter,
            evaluator=evaluator,
            openai_key=openai_key,
        )
        result = worker.run()
        if plan.artifact_expectation is not None:
            result = _attach_artifact_verdict(result, plan, tool_runner)
        return result
    except Exception as exc:
        tb = traceback.format_exc()
        return _error_result(plan, f"{exc}\n{tb[:300]}")


def _attach_artifact_verdict(
    result: ConversationResult,
    plan: ConversationPlan,
    tool_runner: Optional[MockToolRunner],
) -> ConversationResult:
    """Construye el PDF sintetico, lo audita y mezcla score de artefacto."""
    expectation = plan.artifact_expectation
    if expectation is None:
        return result

    try:
        calls = tool_runner.calls if tool_runner is not None else []
        pdf_bytes = build_synthetic_pdf(expectation.canonical_data, calls)
        verdict = verify_inline_pdf(
            cliente=expectation.cliente,
            artifact_id=expectation.artifact_id,
            pdf_bytes=pdf_bytes,
            expected_snapshot=expectation.expected_snapshot,
            extra_metadata={
                "plan_id": result.plan_id,
                "category": result.category,
                "tool_calls": len(calls),
            },
        )
    except VerificadorUnavailable as exc:
        result.artifact_verdict = {
            "status": "skipped",
            "skip_reason": "verificador_unavailable",
            "artifact_id": expectation.artifact_id,
            "error": str(exc)[:300],
        }
        return result
    except Exception as exc:
        result.artifact_verdict = {
            "status": "skipped",
            "skip_reason": "artifact_postphase_error",
            "artifact_id": expectation.artifact_id,
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
        return result

    artifact_score = max(0.0, min(1.0, float(verdict.get("score") or 0.0)))
    weight = expectation.weight
    result.overall_score = round(
        result.overall_score * (1.0 - weight) + artifact_score * weight,
        3,
    )
    result.passed = result.overall_score >= plan.success_threshold
    # Adjuntar el snapshot esperado para que el reporter pueda hacer
    # side-by-side esperado vs observado. El Verificador no lo devuelve.
    if isinstance(verdict, dict) and "expected_snapshot" not in verdict:
        verdict["expected_snapshot"] = expectation.expected_snapshot
    result.artifact_verdict = verdict
    return result


def _build_batch_result(
    batch: ConversationBatch,
    results: List[ConversationResult],
    cost_meter: Optional[CostMeter] = None,
) -> BatchResult:
    """Construye el BatchResult consolidado."""
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    pass_rate = passed / len(results) if results else 0.0

    # Resultados por categoría
    by_category: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in results:
        by_category[r.category]["total"] += 1
        if r.passed:
            by_category[r.category]["passed"] += 1
    for cat, data in by_category.items():
        data["pass_rate"] = (
            data["passed"] / data["total"] if data["total"] > 0 else 0.0
        )

    # Patrón de colapso — en qué turnos falla más
    collapse_pattern: Dict[str, int] = defaultdict(int)
    for r in results:
        if r.collapse_turn is not None:
            collapse_pattern[f"turno_{r.collapse_turn}"] += 1

    # Scorecard por métrica
    metric_scores: Dict[str, List[float]] = defaultdict(list)
    for r in results:
        for tr in r.turn_results:
            for metric, score in tr.scores.items():
                metric_scores[metric].append(score)
    scorecard = {
        metric: round(sum(scores) / len(scores), 3)
        for metric, scores in metric_scores.items()
        if scores
    }
    artifact_scores = [
        float(r.artifact_verdict.get("score"))
        for r in results
        if r.artifact_verdict and r.artifact_verdict.get("score") is not None
    ]
    if artifact_scores:
        scorecard["artifact_verification"] = round(
            sum(artifact_scores) / len(artifact_scores), 3
        )

    # Resumen de costo/tokens — solo si hubo tracking (modo e2e).
    cost_summary: Optional[Dict[str, Any]] = None
    if cost_meter is not None:
        meter_data = cost_meter.summary()
        if meter_data.get("total_calls", 0) > 0:
            cost_summary = meter_data
            scorecard["tokens_total"] = float(meter_data["total_tokens"])
            scorecard["estimated_cost_usd"] = float(meter_data["total_cost_usd"])

    # Recomendaciones basadas en patrones de fallo
    recommendations = _build_recommendations(results, by_category, collapse_pattern)

    return BatchResult(
        batch_id=batch.batch_id,
        agent_id=batch.agent_id,
        total=len(results),
        passed=passed,
        failed=failed,
        pass_rate=round(pass_rate, 3),
        by_category=dict(by_category),
        collapse_pattern=dict(collapse_pattern),
        results=results,
        recommendations=recommendations,
        scorecard=scorecard,
        cost_summary=cost_summary,
    )


def _build_recommendations(
    results: List[ConversationResult],
    by_category: Dict[str, Any],
    collapse_pattern: Dict[str, int],
) -> List[str]:
    """Genera recomendaciones basadas en patrones de fallo."""
    recs = []

    # Categorías con pass rate < 50%
    failing_cats = [
        cat for cat, data in by_category.items()
        if data.get("pass_rate", 1.0) < 0.50 and data.get("total", 0) >= 2
    ]
    if failing_cats:
        recs.append(
            f"Categorías críticas (pass rate < 50%): {', '.join(failing_cats)}. "
            "Revisar el system prompt para mejorar el manejo de estos escenarios."
        )

    # Turno de colapso más frecuente
    if collapse_pattern:
        top_collapse = max(collapse_pattern, key=collapse_pattern.get)
        top_count = collapse_pattern[top_collapse]
        recs.append(
            f"Colapso más frecuente en {top_collapse} ({top_count} veces). "
            "El agente pierde el hilo en conversaciones de múltiples turnos."
        )

    # Métricas con score bajo
    metric_fails: Dict[str, List[float]] = defaultdict(list)
    for r in results:
        for tr in r.turn_results:
            for metric, score in tr.scores.items():
                metric_fails[metric].append(score)

    for metric, scores in metric_fails.items():
        avg = sum(scores) / len(scores)
        if avg < 0.60:
            label = {
                "context_memory": "El agente olvida datos dados en turnos anteriores. "
                                  "Agregar al system prompt: recordar explícitamente datos del usuario.",
                "tool_invocation": "El agente no invoca las tools cuando tiene los datos. "
                                   "Revisar la descripción y campos requeridos de cada webhook.",
                "boundary_respect": "El agente responde preguntas fuera de su dominio. "
                                    "Agregar reglas explícitas de rechazo en el system prompt.",
                "tone_management": "El agente pierde compostura bajo presión. "
                                   "Agregar ejemplos de manejo de usuarios agresivos.",
            }.get(metric, f"Métrica '{metric}' con score promedio bajo ({avg:.2f}).")
            recs.append(label)

    if not recs:
        recs.append("El agente muestra buen desempeño general en las conversaciones evaluadas.")

    return recs[:5]  # máximo 5 recomendaciones


def ejecutar_batch(
    batch: ConversationBatch,
    adapter_factory: Callable,
    evaluator: TurnEvaluator,
    on_progress: Optional[Callable] = None,
    openai_key: str = "",
    conv_timeout_s: float = 120.0,
    synthetic_context: Optional[Dict[str, Any]] = None,
) -> BatchResult:
    """Ejecuta todas las conversaciones del batch con concurrencia controlada.

    Args:
        batch: ConversationBatch con todos los planes
        adapter_factory: fn(adapter_type, agent_id) → adapter instance
        evaluator: TurnEvaluator compartido (stateless)
        on_progress: callback opcional fn(completed, total, last_result)
        openai_key: OpenAI API key para diagnósticos
        conv_timeout_s: timeout máximo por conversación (default 120s)
    """
    results: List[ConversationResult] = []
    total = len(batch.plans)
    completed = 0

    # CostMeter compartido por todo el batch. Si ningún plan es e2e, queda
    # vacío y _build_batch_result lo descarta.
    cost_meter = CostMeter()

    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Iniciando batch {batch.batch_id}: {total} conversaciones, "
        f"concurrencia={batch.concurrency}"
    )

    with ThreadPoolExecutor(max_workers=batch.concurrency) as executor:
        futures = {
            executor.submit(
                _run_single_conversation,
                plan,
                adapter_factory(batch.adapter, batch.agent_id),
                evaluator,
                openai_key,
                synthetic_context,
                cost_meter,
            ): plan
            for plan in batch.plans
        }

        for future in as_completed(futures):
            plan = futures[future]
            try:
                result = future.result(timeout=conv_timeout_s)
                results.append(result)
            except Exception as exc:
                results.append(_error_result(plan, str(exc)))

            completed += 1
            last_result = results[-1]
            status = "OK " if last_result.passed else "FAIL"
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[{status}] {completed}/{total} — {last_result.plan_id} "
                f"({last_result.category}) score={last_result.overall_score:.2f}"
            )

            if on_progress:
                try:
                    on_progress(completed, total, last_result)
                except Exception:
                    pass

    return _build_batch_result(batch, results, cost_meter=cost_meter)
