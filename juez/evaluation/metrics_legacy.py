from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Iterable

from deepeval.metrics import (
    AnswerRelevancyMetric,
    FaithfulnessMetric,
    ContextualPrecisionMetric,
    HallucinationMetric,
    GEval,
)
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from openai import OpenAI

try:
    from .config import CONFIG
except Exception:
    from config import CONFIG


@dataclass(frozen=True)
class MetricOutcome:
    name: str
    score: float | None
    threshold: float | None
    reason: str | None
    success: bool
    error: str | None = None


_translate_cache: dict[str, str] = {}
_translator = OpenAI(api_key=CONFIG.openai_api_key) if CONFIG.openai_api_key else None


def _safe_init(metric_cls: type, **kwargs: Any) -> Any:
    sig = inspect.signature(metric_cls.__init__)
    filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return metric_cls(**filtered)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_success(metric: Any, score: float | None, threshold: float | None) -> bool:
    is_successful = getattr(metric, "is_successful", None)
    if callable(is_successful):
        try:
            return bool(is_successful())
        except Exception:
            pass
    if score is None or threshold is None:
        return False
    return score >= threshold


def _to_spanish(text: str | None) -> str | None:
    if not text or CONFIG.output_lang.lower() != "es":
        return text
    if text in _translate_cache:
        return _translate_cache[text]
    if _translator is None:
        return text
    try:
        completion = _translator.chat.completions.create(
            model=CONFIG.eval_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Traduce al español en una sola oración clara. "
                        "Conserva números y nombres propios."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0,
        )
        translated = completion.choices[0].message.content or text
        _translate_cache[text] = translated
        return translated
    except Exception:
        return text


def _build_instruction_metric() -> Any:
    criteria = (
        "Evalúa si la salida cumple exactamente las instrucciones del usuario y "
        "el comportamiento esperado. Considera restricciones, formato y completitud. "
        "Responder directamente es suficiente; no es necesario repetir la pregunta del usuario."
    )
    evaluation_steps = [
        "Identifica la instrucción principal del usuario.",
        "Verifica si la salida la cumple de forma directa.",
        "Comprueba alineación con el expected_behavior.",
        "Penaliza omisiones, relleno o desviaciones.",
    ]
    evaluation_params = [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ]
    return _safe_init(
        GEval,
        name="Instruction Adherence",
        criteria=criteria,
        evaluation_steps=evaluation_steps,
        evaluation_params=evaluation_params,
        threshold=CONFIG.instruction_adherence_threshold,
        model=CONFIG.eval_model,
        strict_mode=CONFIG.strict_mode,
    )


def _build_judgement_coherence_metric() -> Any:
    criteria = (
        "Evalúa la coherencia del juicio: si la respuesta cumple lo pedido sin "
        "contradicciones ni relleno. Permite incluir elementos relacionados "
        "cuando el usuario pide una categoria amplia (por ejemplo, productos de limpieza). "
        "Responder directamente es válido sin repetir la pregunta."
    )
    evaluation_steps = [
        "Determina si la respuesta cumple exactamente lo pedido.",
        "Detecta contradicciones, omisiones graves o relleno innecesario.",
        "Evalúa concisión y estructura si se pidió un formato específico.",
    ]
    evaluation_params = [
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ]
    return _safe_init(
        GEval,
        name="Coherencia de Juicio",
        criteria=criteria,
        evaluation_steps=evaluation_steps,
        evaluation_params=evaluation_params,
        threshold=CONFIG.judgement_coherence_threshold,
        model=CONFIG.eval_model,
        strict_mode=CONFIG.strict_mode,
    )


def build_metrics(has_context: bool) -> list[Any]:
    metrics: list[Any] = []
    metrics.append(
        _safe_init(
            AnswerRelevancyMetric,
            threshold=CONFIG.relevancy_threshold,
            model=CONFIG.eval_model,
            include_reason=True,
            verbose_mode=False,
            strict_mode=CONFIG.strict_mode,
            async_mode=False,
        )
    )
    if has_context:
        metrics.append(
            _safe_init(
                FaithfulnessMetric,
                threshold=CONFIG.faithfulness_threshold,
                model=CONFIG.eval_model,
                include_reason=True,
                verbose_mode=False,
                strict_mode=CONFIG.strict_mode,
                async_mode=False,
            )
        )
        metrics.append(
            _safe_init(
                ContextualPrecisionMetric,
                threshold=CONFIG.contextual_precision_threshold,
                model=CONFIG.eval_model,
                include_reason=True,
                verbose_mode=False,
                strict_mode=CONFIG.strict_mode,
                async_mode=False,
            )
        )
        metrics.append(
            _safe_init(
                HallucinationMetric,
                threshold=CONFIG.hallucination_threshold,
                model=CONFIG.eval_model,
                include_reason=True,
                verbose_mode=False,
                strict_mode=CONFIG.strict_mode,
                async_mode=False,
            )
        )
    metrics.append(_build_instruction_metric())
    metrics.append(_build_judgement_coherence_metric())
    return metrics


def evaluate_metrics(test_case: LLMTestCase, metrics: Iterable[Any]) -> list[MetricOutcome]:
    outcomes: list[MetricOutcome] = []
    if not CONFIG.openai_api_key:
        for metric in metrics:
            name = getattr(metric, "name", metric.__class__.__name__)
            threshold = _to_float(getattr(metric, "threshold", None))
            outcomes.append(
                MetricOutcome(
                    name=name,
                    score=None,
                    threshold=threshold,
                    reason="Métrica omitida por falta de OPENAI_API_KEY.",
                    success=True,
                    error=None,
                )
            )
        return outcomes
    for metric in metrics:
        name = getattr(metric, "name", metric.__class__.__name__)
        threshold = _to_float(getattr(metric, "threshold", None))
        try:
            metric.measure(test_case)
            score = _to_float(getattr(metric, "score", None))
            reason = _to_spanish(getattr(metric, "reason", None))
            success = _metric_success(metric, score, threshold)
            outcomes.append(
                MetricOutcome(
                    name=name,
                    score=score,
                    threshold=threshold,
                    reason=reason,
                    success=success,
                    error=None,
                )
            )
        except Exception as exc:
            error_text = _to_spanish(str(exc))
            error_lower = (error_text or "").lower()
            if any(
                token in error_lower
                for token in ["apiconnectionerror", "connection error", "retryerror", "timeout"]
            ):
                outcomes.append(
                    MetricOutcome(
                        name=name,
                        score=None,
                        threshold=threshold,
                        reason="Métrica omitida por fallo de conexión con el modelo evaluador.",
                        success=True,
                        error=error_text,
                    )
                )
            else:
                outcomes.append(
                    MetricOutcome(
                        name=name,
                        score=None,
                        threshold=threshold,
                        reason=None,
                        success=False,
                        error=error_text,
                    )
                )
    return outcomes
