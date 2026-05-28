from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv()

os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "180")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")
os.environ.setdefault("POSTHOG_DISABLED", "1")
os.environ.setdefault("OPENAI_LOG", "error")

from deepeval.test_case import LLMTestCase
from .utils.text_normalization import repair_recursive

try:
    from .config import CONFIG
    from .metrics import MetricOutcome, build_metrics, evaluate_metrics
    from .test_cases import AgentTestCase, load_test_cases
except Exception:
    from config import CONFIG
    from metrics import MetricOutcome, build_metrics, evaluate_metrics
    from test_cases import AgentTestCase, load_test_cases


logging.basicConfig(level=CONFIG.log_level, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("deepeval").setLevel(logging.WARNING)
logger = logging.getLogger("evaluation")


@dataclass(frozen=True)
class CaseResult:
    index: int
    input: str
    expected_behavior: str
    context: list[str]
    response: str | None
    retrieval_context: list[str]
    metric_results: list[MetricOutcome]
    success: bool
    error: str | None
    duration_s: float
    turn_results: list["TurnResult"] | None = None


@dataclass(frozen=True)
class TurnResult:
    turn_index: int
    user: str
    assistant: str
    metric_results: list[MetricOutcome]
    success: bool


@dataclass(frozen=True)
class EvaluationReport:
    results: list[CaseResult]
    total_time_s: float
    passed: int
    failed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_openai_key() -> None:
    if not CONFIG.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Set it in the environment before running evaluation."
        )


def load_run_agent() -> Callable[[str], dict]:
    module = importlib.import_module(CONFIG.agent_module)
    fn = getattr(module, CONFIG.agent_function, None)
    if fn is None or not callable(fn):
        raise AttributeError(
            f"Function '{CONFIG.agent_function}' not found in module '{CONFIG.agent_module}'."
        )
    return fn


def _call_agent(run_agent: Callable[[str], dict], user_input: str) -> tuple[str, list[str]]:
    result = run_agent(user_input)
    if not isinstance(result, dict):
        raise TypeError("run_agent must return a dict with keys: response, retrieval_context")
    response = result.get("response", None)
    if response is None:
        raise KeyError("run_agent return dict is missing 'response'")
    retrieval_context = result.get("retrieval_context") or []
    if isinstance(retrieval_context, (str, bytes)):
        retrieval_context = [retrieval_context]
    if not isinstance(retrieval_context, list):
        retrieval_context = [str(retrieval_context)]
    retrieval_list = [str(item) for item in retrieval_context if item is not None]
    return str(response), retrieval_list


def _build_llm_test_case(
    test_case: AgentTestCase,
    response: str,
    context_for_eval: list[str],
    retrieval_context: list[str],
) -> LLMTestCase:
    kwargs = {
        "input": test_case.input,
        "actual_output": response,
        "expected_output": test_case.expected_behavior,
        "context": context_for_eval,
        "retrieval_context": retrieval_context,
    }
    return LLMTestCase(**kwargs)

def _parse_conversation(text: str) -> list[str]:
    lines = [l.strip() for l in text.splitlines()]
    if not any(
        l.lower().startswith("conversación")
        or l.lower().startswith("conversacion")
        or l.lower().startswith("conversaciÃ³n")
        for l in lines
    ):
        return []
    user_msgs: list[str] = []
    for line in lines:
        if line.lower().startswith("usuario:"):
            msg = line.split(":", 1)[1].strip()
            if msg:
                user_msgs.append(msg)
    return user_msgs


def evaluate_case(
    run_agent: Callable[[str], dict],
    test_case: AgentTestCase,
    index: int,
) -> CaseResult:
    start = time.perf_counter()
    response: str | None = None
    retrieval_context: list[str] = []
    metric_results: list[MetricOutcome] = []
    error: str | None = None
    success = False

    try:
        user_turns = _parse_conversation(test_case.input)
        if user_turns:
            turn_results: list[TurnResult] = []
            all_ok = True
            for i, user_msg in enumerate(user_turns, start=1):
                response, retrieval_context = _call_agent(run_agent, user_msg)
                if not retrieval_context and test_case.context:
                    retrieval_context = list(test_case.context)
                context_for_eval = retrieval_context if retrieval_context else list(test_case.context)
                llm_test_case = _build_llm_test_case(
                    test_case=AgentTestCase(
                        input=user_msg,
                    expected_behavior=(
                        "Responde de forma directa y concisa. "
                        "No repitas la pregunta. "
                        f"Usuario: {user_msg}"
                    ),
                        context=list(test_case.context),
                    ),
                    response=response,
                    context_for_eval=context_for_eval,
                    retrieval_context=retrieval_context,
                )
                metrics = build_metrics(has_context=bool(context_for_eval))
                results = evaluate_metrics(llm_test_case, metrics)
                ok = all(m.success for m in results) if results else False
                if CONFIG.fail_on_any_metric:
                    all_ok = all_ok and ok
                else:
                    all_ok = all_ok or ok
                turn_results.append(
                    TurnResult(
                        turn_index=i,
                        user=user_msg,
                        assistant=response,
                        metric_results=results,
                        success=ok,
                    )
                )
            metric_results = []
            response = None
            retrieval_context = []
            success = all_ok
            return CaseResult(
                index=index,
                input=test_case.input,
                expected_behavior=test_case.expected_behavior,
                context=list(test_case.context),
                response=response,
                retrieval_context=retrieval_context,
                metric_results=metric_results,
                success=success,
                error=None,
                duration_s=time.perf_counter() - start,
                turn_results=turn_results,
            )

        response, retrieval_context = _call_agent(run_agent, test_case.input)
        if not retrieval_context and test_case.context:
            retrieval_context = list(test_case.context)
        context_for_eval = retrieval_context if retrieval_context else list(test_case.context)
        llm_test_case = _build_llm_test_case(
            test_case=test_case,
            response=response,
            context_for_eval=context_for_eval,
            retrieval_context=retrieval_context,
        )
        metrics = build_metrics(has_context=bool(context_for_eval))
        metric_results = evaluate_metrics(llm_test_case, metrics)
        if metric_results:
            if CONFIG.fail_on_any_metric:
                success = all(m.success for m in metric_results)
            else:
                success = any(m.success for m in metric_results)
        else:
            success = False
    except Exception as exc:
        error = str(exc)
        logger.exception("Case %s failed: %s", index, exc)

    duration = time.perf_counter() - start
    return CaseResult(
        index=index,
        input=test_case.input,
        expected_behavior=test_case.expected_behavior,
        context=list(test_case.context),
        response=response,
        retrieval_context=retrieval_context,
        metric_results=metric_results,
        success=success,
        error=error,
        duration_s=duration,
    )


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _aggregate_metrics(results: list[CaseResult]) -> dict[str, dict[str, float]]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    fails: dict[str, int] = {}
    for r in results:
        for m in r.metric_results:
            name = m.name
            if m.score is not None:
                totals[name] = totals.get(name, 0.0) + float(m.score)
                counts[name] = counts.get(name, 0) + 1
            if not m.success:
                fails[name] = fails.get(name, 0) + 1
    avg: dict[str, float] = {}
    for name, total in totals.items():
        avg[name] = total / max(counts.get(name, 1), 1)
    return {"avg": avg, "fails": fails}


def _global_recommendation(results: list[CaseResult], limit: int = 3) -> str:
    reasons: list[str] = []
    for r in results:
        for m in r.metric_results:
            if not m.success:
                reason = m.reason or m.error
                if reason:
                    reasons.append(reason)
    if not reasons:
        return "Todo bien. No se sugieren cambios globales."
    dedup: list[str] = []
    for r in reasons:
        if r not in dedup:
            dedup.append(r)
        if len(dedup) >= limit:
            break
    return "Mejoras prioritarias:\n- " + "\n- ".join(dedup)


def _case_recommendation(result: CaseResult, limit: int = 3) -> str:
    reasons: list[str] = []
    for m in result.metric_results:
        if not m.success:
            reason = m.reason or m.error
            if reason:
                reasons.append(reason)
    if not reasons:
        return "Todo bien. No se sugieren cambios."
    dedup: list[str] = []
    for r in reasons:
        if r not in dedup:
            dedup.append(r)
        if len(dedup) >= limit:
            break
    return "Mejoras sugeridas:\n- " + "\n- ".join(dedup)


def _turn_recommendation(metrics: Iterable[MetricOutcome], limit: int = 3) -> str:
    reasons: list[str] = []
    for m in metrics:
        if not m.success:
            reason = m.reason or m.error
            if reason:
                reasons.append(reason)
    if not reasons:
        return "Todo bien. No se sugieren cambios."
    dedup: list[str] = []
    for r in reasons:
        if r not in dedup:
            dedup.append(r)
        if len(dedup) >= limit:
            break
    return "Mejoras sugeridas:\n- " + "\n- ".join(dedup)


def print_evaluation_report(report: EvaluationReport) -> None:
    print("=" * 80)
    print("Evaluación DeepEval del Agente")
    print(f"Modelo: {CONFIG.eval_model}")
    print(f"Casos: {len(report.results)}")
    print("=" * 80)
    print("")

    for result in report.results:
        status = "OK" if result.success else "FALLO"
        print("-" * 80)
        print(f"CASO {result.index} - {status}")
        print("-" * 80)
        print("Entrada:")
        print(_truncate(result.input, 300))
        if result.turn_results:
            for turn in result.turn_results:
                t_status = "OK" if turn.success else "FALLO"
                print("")
                print(f"Turno {turn.turn_index}: {t_status}")
                print("Usuario:")
                print(_truncate(turn.user, 300))
                print("Asistente:")
                print(_truncate(turn.assistant, 400))
                print("Métricas:")
                print("  Nombre | Estado | Puntaje | Umbral")
                print("  " + "-" * 34)
                for metric in turn.metric_results:
                    m_status = "OK" if metric.success else "FALLO"
                    score = "n/a" if metric.score is None else f"{metric.score:.3f}"
                    threshold = "n/a" if metric.threshold is None else f"{metric.threshold:.3f}"
                    print(f"  {metric.name} | {m_status} | {score} | {threshold}")
                    if CONFIG.print_reasons:
                        reason = metric.reason or metric.error or "Sin motivo."
                        print("  Motivo:")
                        print(f"  {reason}")
                print("Recomendación del turno:")
                print(_turn_recommendation(turn.metric_results))
                print("")
            print(f"Duración del caso: {result.duration_s:.2f}s")
            print("")
            continue
        if result.error:
            print(f"Error: {result.error}")
            print(f"Duración: {result.duration_s:.2f}s")
            print("")
            continue

        if result.response is not None:
            print("Respuesta:")
            print(_truncate(result.response, 300))
        if result.retrieval_context:
            print(f"Contextos usados: {len(result.retrieval_context)}")

        print("Métricas:")
        print("  Nombre | Estado | Puntaje | Umbral")
        print("  " + "-" * 34)
        for metric in result.metric_results:
            m_status = "OK" if metric.success else "FALLO"
            score = "n/a" if metric.score is None else f"{metric.score:.3f}"
            threshold = "n/a" if metric.threshold is None else f"{metric.threshold:.3f}"
            print(f"  {metric.name} | {m_status} | {score} | {threshold}")
            if CONFIG.print_reasons:
                reason = metric.reason or metric.error or "Sin motivo."
                print("  Motivo:")
                print(f"  {reason}")

        print("Recomendación del caso:")
        print(_case_recommendation(result))
        print(f"Duración del caso: {result.duration_s:.2f}s")
        print("")

    aggregates = _aggregate_metrics(report.results)
    avg = aggregates["avg"]
    fails = aggregates["fails"]
    print("=" * 80)
    print("Resumen global")
    print("=" * 80)
    if avg:
        print("Promedios por métrica:")
        for name, value in sorted(avg.items(), key=lambda x: x[0]):
            print(f"- {name}: {value:.3f}")
    if fails:
        print("Métricas con más fallos:")
        for name, count in sorted(fails.items(), key=lambda x: x[1], reverse=True)[:3]:
            print(f"- {name}: {count}")
    print("Recomendación global:")
    print(_global_recommendation(report.results))
    print("")
    print(f"Tiempo total: {report.total_time_s:.2f}s")
    print(f"OK: {report.passed}  Fallaron: {report.failed}")


def export_report(report: EvaluationReport) -> Path:
    path = CONFIG.export_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        data = repair_recursive(report.to_dict())
        json.dump(data, f, ensure_ascii=True, indent=2)
    return path


def run_all_cases(*, export: bool | None = None, print_report: bool = True) -> EvaluationReport:
    test_cases = load_test_cases()
    if export is None:
        export = CONFIG.export_results_json

    try:
        run_agent = load_run_agent()
    except Exception as exc:
        message = f"Failed to load run_agent: {exc}"
        logger.exception(message)
        results = [
            CaseResult(
                index=i,
                input=tc.input,
                expected_behavior=tc.expected_behavior,
                context=list(tc.context),
                response=None,
                retrieval_context=[],
                metric_results=[],
                success=False,
                error=message,
                duration_s=0.0,
            )
            for i, tc in enumerate(test_cases, start=1)
        ]
        report = EvaluationReport(
            results=results,
            total_time_s=0.0,
            passed=0,
            failed=len(results),
        )
        if print_report:
            print_evaluation_report(report)
        if export:
            export_report(report)
        return report

    require_openai_key()
    start_total = time.perf_counter()
    results: list[CaseResult] = []

    for idx, test_case in enumerate(test_cases, start=1):
        result = evaluate_case(run_agent, test_case, idx)
        results.append(result)
        if CONFIG.fail_fast and not result.success:
            break

    total_time = time.perf_counter() - start_total
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed
    report = EvaluationReport(results=results, total_time_s=total_time, passed=passed, failed=failed)

    if print_report:
        print_evaluation_report(report)
    if export:
        export_report(report)

    return report


def main() -> int:
    try:
        report = run_all_cases(export=CONFIG.export_results_json, print_report=True)
    except Exception as exc:
        logger.exception("Evaluation failed: %s", exc)
        print(f"ERROR: {exc}")
        return 1
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
