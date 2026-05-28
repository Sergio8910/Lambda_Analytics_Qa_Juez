from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


FAITHFULNESS_THRESHOLD = _get_env_float("FAITHFULNESS_THRESHOLD", 0.90)
RELEVANCY_THRESHOLD = _get_env_float("RELEVANCY_THRESHOLD", 0.83)
CONTEXTUAL_PRECISION_THRESHOLD = _get_env_float("CONTEXTUAL_PRECISION_THRESHOLD", 0.90)
HALLUCINATION_THRESHOLD = _get_env_float("HALLUCINATION_THRESHOLD", 0.90)
INSTRUCTION_ADHERENCE_THRESHOLD = _get_env_float("INSTRUCTION_ADHERENCE_THRESHOLD", 0.85)
JUDGEMENT_COHERENCE_THRESHOLD = _get_env_float("JUDGEMENT_COHERENCE_THRESHOLD", 0.85)

FAIL_ON_ANY_METRIC = _get_env_bool("FAIL_ON_ANY_METRIC", True)
STRICT_MODE = _get_env_bool("STRICT_MODE", False)
OUTPUT_LANG = _get_env_str("OUTPUT_LANG", "es")

EVAL_MODEL = _get_env_str("EVAL_MODEL", _get_env_str("DEEPEVAL_MODEL", "gpt-4o-mini"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

EXPORT_RESULTS_JSON = _get_env_bool("EVAL_EXPORT_JSON", False)
EXPORT_PATH = Path(_get_env_str("EVAL_EXPORT_PATH", "evaluation/results.json"))

AGENT_MODULE = _get_env_str("AGENT_MODULE", "agent")
AGENT_FUNCTION = _get_env_str("AGENT_FUNCTION", "run_agent")

LOG_LEVEL = _get_env_str("EVAL_LOG_LEVEL", "INFO")
PRINT_REASONS = _get_env_bool("EVAL_PRINT_REASONS", True)
FAIL_FAST = _get_env_bool("EVAL_FAIL_FAST", False)


@dataclass(frozen=True)
class EvalConfig:
    faithfulness_threshold: float
    relevancy_threshold: float
    contextual_precision_threshold: float
    hallucination_threshold: float
    instruction_adherence_threshold: float
    judgement_coherence_threshold: float
    eval_model: str
    openai_api_key: str | None
    export_results_json: bool
    export_path: Path
    agent_module: str
    agent_function: str
    log_level: str
    print_reasons: bool
    fail_fast: bool
    fail_on_any_metric: bool
    strict_mode: bool
    output_lang: str


CONFIG = EvalConfig(
    faithfulness_threshold=FAITHFULNESS_THRESHOLD,
    relevancy_threshold=RELEVANCY_THRESHOLD,
    contextual_precision_threshold=CONTEXTUAL_PRECISION_THRESHOLD,
    hallucination_threshold=HALLUCINATION_THRESHOLD,
    instruction_adherence_threshold=INSTRUCTION_ADHERENCE_THRESHOLD,
    judgement_coherence_threshold=JUDGEMENT_COHERENCE_THRESHOLD,
    eval_model=EVAL_MODEL,
    openai_api_key=OPENAI_API_KEY,
    export_results_json=EXPORT_RESULTS_JSON,
    export_path=EXPORT_PATH,
    agent_module=AGENT_MODULE,
    agent_function=AGENT_FUNCTION,
    log_level=LOG_LEVEL,
    print_reasons=PRINT_REASONS,
    fail_fast=FAIL_FAST,
    fail_on_any_metric=FAIL_ON_ANY_METRIC,
    strict_mode=STRICT_MODE,
    output_lang=OUTPUT_LANG,
)
