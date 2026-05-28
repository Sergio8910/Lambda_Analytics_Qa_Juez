from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Carga variables de entorno antes de importar DeepEval.
load_dotenv()

# Desactiva telemetría antes de cargar DeepEval (si aplica).
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")

from .case_factory import build_cases
from .judge_engine import JudgeEngine
from .metamorphic import build_variants
from .report_models import EvaluationSpec, TestCase
from .report_writer import pretty_print_summary
from .runner import run_agent
from .utils.text_normalization import repair_recursive


DEFAULT_SPEC_PATH = Path(__file__).resolve().parent / "testdata" / "spec_smoke.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke run del evaluador.")
    parser.add_argument(
        "--spec",
        type=str,
        default=str(DEFAULT_SPEC_PATH),
        help="Ruta del spec JSON (por defecto spec_smoke.json).",
    )
    args = parser.parse_args()
    spec_path = Path(args.spec)
    spec_data = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    spec_data = repair_recursive(spec_data)
    spec = EvaluationSpec(**spec_data)

    cases = build_cases(spec)
    if spec.enable_metamorphic:
        all_cases: List[TestCase] = []
        for tc in cases:
            all_cases.append(tc)
            all_cases.extend(build_variants(tc, spec.metamorphic_variants_per_case, spec.seed))
        cases = all_cases

    engine = JudgeEngine(spec)
    report = engine.evaluate_run(cases, lambda x: run_agent(spec, x))
    pretty_print_summary(report)

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY no configurada. Métricas LLM en modo heurístico (skipped).")
        for case in report.cases[:1]:
            skipped = [m.name for m in case.metrics if m.raw.get("skipped")]
            if skipped:
                print("Métricas marcadas como skipped en el primer caso:")
                for name in skipped:
                    print(f"- {name}")
            else:
                print("No se encontraron métricas marcadas como skipped en el primer caso.")

    # Top 5 fallos por métrica
    fallos = report.summary.by_metric_failures
    if fallos:
        print("Top 5 fallos por métrica:")
        for name, count in sorted(fallos.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"- {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
