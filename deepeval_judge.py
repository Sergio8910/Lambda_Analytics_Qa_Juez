from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv()

os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "300")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

try:
    from evaluation.config import CONFIG
except Exception:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Cfg:
        faithfulness_threshold: float = 0.9
        relevancy_threshold: float = 0.85
        contextual_precision_threshold: float = 0.85
        hallucination_threshold: float = 0.85
        instruction_adherence_threshold: float = 0.7
        eval_model: str = "gpt-4o-mini"

    CONFIG = _Cfg()

from evaluation.judge_engine import JudgeEngine
from evaluation.report_models import EvaluationSpec, MetricSpec, TestCase
from evaluation.runner import RunnerResult


def evaluate_response(
    user_input: str,
    model_output: str,
    expected_behavior: Optional[str] = None,
    context: Optional[List[str]] = None,
) -> dict[str, Any]:
    context = context or []
    spec = EvaluationSpec(
        run_id="cli",
        mode="deterministic",
        num_tests=1,
        metrics=[
            MetricSpec(name="answer_relevancy", threshold=CONFIG.relevancy_threshold, enabled=True, weight=1.0, config={}),
            MetricSpec(name="instruction_adherence", threshold=CONFIG.instruction_adherence_threshold, enabled=True, weight=1.0, config={}),
            MetricSpec(name="task_success", threshold=CONFIG.instruction_adherence_threshold, enabled=True, weight=1.0, config={}),
            MetricSpec(name="unsupported_claims", threshold=0.7, enabled=True, weight=1.0, config={}),
            MetricSpec(name="faithfulness", threshold=CONFIG.faithfulness_threshold, enabled=True, weight=1.0, config={}),
            MetricSpec(name="contextual_precision", threshold=CONFIG.contextual_precision_threshold, enabled=True, weight=1.0, config={}),
            MetricSpec(name="hallucination", threshold=CONFIG.hallucination_threshold, enabled=True, weight=1.0, config={}),
        ],
    )
    engine = JudgeEngine(spec)
    testcase = TestCase(
        case_id="cli-1",
        input=user_input,
        tags=["cli"],
        severity="media",
        expected_behavior=expected_behavior or "",
        context=context,
    )
    rr = RunnerResult(output_text=model_output, retrieval_context=context, latency_ms=0.0, error=None)
    report = engine.evaluate_case(testcase, rr)
    results = [
        {
            "name": m.name,
            "score": m.score,
            "threshold": m.threshold,
            "reason": m.reason or m.error,
            "success": m.success,
        }
        for m in report.metrics
    ]
    recommendation = _to_prompt_recommendation(results)
    return {"metrics": results, "recommendation": recommendation}


def _to_prompt_recommendation(results: Iterable[dict[str, Any]]) -> str:
    fails = [r for r in results if not r.get("success")]
    if not fails:
        return "Todo bien. No se sugieren cambios al prompt."
    tips = []
    for r in fails:
        reason = r.get("reason") or "No se proporcionó motivo."
        tips.append(f"- Mejora {r.get('name')}: {reason}")
    return "Considera estas mejoras:\n" + "\n".join(tips)


def _load_context_from_file(path: Path) -> List[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, str):
            return [data]
    except Exception:
        return []
    return []


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a quick DeepEval judge on a single pair.")
    p.add_argument("--input", required=True, help="User input text")
    p.add_argument("--output", required=True, help="Model output text")
    p.add_argument("--expected", default="", help="Expected behavior/intent (optional)")
    p.add_argument(
        "--context",
        action="append",
        default=[],
        help="Context snippet (can repeat).",
    )
    p.add_argument(
        "--context-file",
        type=str,
        default="",
        help="Path to JSON file with a list of context strings.",
    )
    p.add_argument("--json", action="store_true", help="Print raw JSON instead of text")
    return p.parse_args()


def _main() -> int:
    args = _parse_args()
    context: List[str] = list(args.context)
    if args.context_file:
        context.extend(_load_context_from_file(Path(args.context_file)))

    report = evaluate_response(
        user_input=args.input,
        model_output=args.output,
        expected_behavior=args.expected,
        context=context,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print("Juez DeepEval")
    for m in report["metrics"]:
        status = "OK" if m["success"] else "FALLO"
        score = "n/a" if m["score"] is None else f"{m['score']:.3f}"
        thr = "n/a" if m["threshold"] is None else f"{m['threshold']:.3f}"
        print(f"- {m['name']}: {status} puntaje={score} umbral={thr}")
        if m.get("reason"):
            print(f"  motivo: {m['reason']}")
    print("Recomendación:")
    print(report["recommendation"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
