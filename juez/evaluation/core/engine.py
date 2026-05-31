from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, List

from ..adapters.base import BaseAdapter
from ..adapters.default_callable import DefaultCallableAdapter
from ..contracts import RunnerResult
from ..report_models import CaseReport, EvaluationSpec, MetricResult, RunReport, TestCase
from .engine_impl import JudgeEngine


def _is_empty_result(rr: RunnerResult) -> bool:
    """True si el runner no devolvió respuesta (output vacío o error explícito).

    Es tolerante a runners que devuelven objetos sin atributo ``error``
    (p. ej. mocks en tests): si el atributo no existe, se asume sin error.
    """
    return bool(getattr(rr, "error", None)) or not (getattr(rr, "output_text", "") or "").strip()


def _make_error_report(tc: TestCase, rr: RunnerResult) -> CaseReport:
    """Crea un CaseReport de fallo directo cuando el runner no produjo respuesta."""
    razon = rr.error or "El agente no respondió (output vacío)"
    metric = MetricResult(
        name="agent_response",
        score=0.0,
        threshold=1.0,
        success=False,
        reason=razon,
        reason_es=razon,
        raw={"runner_error": rr.error or "empty_output"},
    )
    return CaseReport(
        case_id=tc.case_id,
        tags=tc.tags,
        severity=tc.severity,
        passed=False,
        metrics=[metric],
        latency_ms=rr.latency_ms,
        input_text=tc.input,
        output_text="",
        expected_behavior=tc.expected_behavior,
    )


class EvaluationEngine:
    def __init__(self, spec: EvaluationSpec, adapter: BaseAdapter | None = None) -> None:
        self.spec = spec
        self.adapter = adapter or DefaultCallableAdapter()
        self.judge = JudgeEngine(spec)

    def evaluate_run(
        self,
        cases: List[TestCase],
        runner: Callable[[TestCase], RunnerResult],
        dump_normalized_run: bool = False,
    ) -> RunReport:
        case_reports: List[CaseReport] = []
        outputs_por_caso = {}
        total_cases = len(cases)

        concurrency = max(1, int(getattr(self.spec, "max_concurrency", 1) or 1))
        can_parallel = (
            concurrency > 1
            and not self.spec.fail_fast
            and not self.spec.llm_fail_fast_on_infra
        )
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"Iniciando evaluación de {total_cases} caso(s) con concurrencia={concurrency}."
        )

        if not can_parallel:
            for idx, tc in enumerate(cases, start=1):
                rr = runner(tc)
                report = _make_error_report(tc, rr) if _is_empty_result(rr) else self.judge.evaluate_case(tc, rr)
                outputs_por_caso[tc.case_id] = rr.output_text
                if dump_normalized_run:
                    normalized = self.adapter.build_normalized_run(tc, rr, self.spec)
                    report.normalized_run = normalized.model_dump(mode="json")
                case_reports.append(report)
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Progreso {idx}/{total_cases}: case_id={tc.case_id} completado. "
                    f"Restan {total_cases - idx}."
                )
                if self.spec.llm_fail_fast_on_infra and self.judge._has_infra_error(report):
                    break
                if self.spec.fail_fast and report.severity.lower() == "alta" and not report.passed:
                    break
        else:
            indexed_results = []

            def _eval_one(idx: int, tc: TestCase):
                rr = runner(tc)
                report = _make_error_report(tc, rr) if _is_empty_result(rr) else self.judge.evaluate_case(tc, rr)
                if dump_normalized_run:
                    normalized = self.adapter.build_normalized_run(tc, rr, self.spec)
                    report.normalized_run = normalized.model_dump(mode="json")
                return idx, tc.case_id, report, rr.output_text

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(_eval_one, idx, tc)
                    for idx, tc in enumerate(cases)
                ]
                completed = 0
                for fut in as_completed(futures):
                    idx, case_id, report, output_text = fut.result()
                    indexed_results.append((idx, case_id, report, output_text))
                    completed += 1
                    print(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"Progreso {completed}/{total_cases}: case_id={case_id} completado. "
                        f"Restan {total_cases - completed}."
                    )

            indexed_results.sort(key=lambda x: x[0])
            for _, case_id, report, output_text in indexed_results:
                outputs_por_caso[case_id] = output_text
                case_reports.append(report)

        self.judge._apply_consistency(case_reports, outputs_por_caso)
        summary = self.judge._build_summary(case_reports)
        return RunReport(summary=summary, cases=case_reports, spec=self.spec)
