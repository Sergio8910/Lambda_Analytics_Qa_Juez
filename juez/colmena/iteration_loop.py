"""Motor iterativo del Project Repair Loop dry-run."""
from __future__ import annotations

from pathlib import Path

from .failure_analyzer import analyze_failures
from .fix_planner import plan_fixes
from .models import RepairIterationResult, RepairLoopConfig, RepairLoopResult
from .project_evaluator import evaluate_project_path
from .repair_report import write_repair_outputs
from .scanner import scan_project
from .test_executor import execute_synthetic_tests
from .test_planner import plan_synthetic_tests


def run_project_repair_loop(
    project_path: Path | str,
    config: RepairLoopConfig | None = None,
    *,
    output_dir: Path | str = "outputs",
) -> RepairLoopResult:
    config = config or RepairLoopConfig()
    root = Path(project_path).resolve()
    project_report = evaluate_project_path(root)
    initial_score = int(round(project_report.score.score))
    test_cases = []
    test_results = []
    diagnoses = []
    proposals = []
    iterations: list[RepairIterationResult] = []

    effective_mode = "dry-run" if config.repair_mode in {"dry-run", "proposal-only", "apply-safe"} else "dry-run"
    notes = []
    if config.repair_mode == "apply-safe":
        notes.append("apply-safe aun no esta habilitado; se ejecuta como dry-run y no se aplican cambios.")

    for iteration in range(1, config.max_iterations + 1):
        inventory = scan_project(root)
        cases = plan_synthetic_tests(inventory, config)
        results = execute_synthetic_tests(root, inventory, cases)
        current_diagnoses = analyze_failures(project_report, results)
        current_proposals = plan_fixes(current_diagnoses, mode=effective_mode)

        test_cases = cases
        test_results.extend(results)
        diagnoses = current_diagnoses
        proposals = current_proposals
        blockers = sum(1 for item in current_diagnoses if item.has_blocker)
        failures = sum(1 for item in results if not item.passed)

        verdict = _iteration_verdict(initial_score, config, blockers, failures)
        iteration_notes = list(notes)
        iteration_notes.append("No se aplicaron cambios; fixes_applied=0 por modo dry-run/proposal-only.")
        if current_proposals:
            iteration_notes.append("Score sin cambios esperados: las propuestas no se materializan en esta fase.")
        iterations.append(
            RepairIterationResult(
                iteration=iteration,
                score_before=initial_score,
                score_after=initial_score,
                verdict=verdict,
                test_cases_generated=len(cases),
                test_cases_executed=len(results),
                failures_found=failures,
                fixes_proposed=len(current_proposals),
                fixes_applied=0,
                blockers_found=blockers,
                notes=iteration_notes,
            )
        )

        if blockers and config.stop_on_blocker:
            break
        if initial_score >= config.min_score_to_pass and not failures:
            break
        if not current_proposals:
            break

    final_verdict = _final_verdict(project_report, diagnoses, initial_score, config)
    result = RepairLoopResult(
        project_path=str(root),
        config=config,
        initial_score=initial_score,
        final_score=initial_score,
        final_verdict=final_verdict,
        readiness_final=project_report.score.status,
        iterations=iterations,
        test_cases=test_cases,
        test_results=test_results,
        diagnoses=diagnoses,
        fix_proposals=proposals,
    )
    return write_repair_outputs(result, output_dir=output_dir)


def _iteration_verdict(
    score: int,
    config: RepairLoopConfig,
    blockers: int,
    failures: int,
):
    if blockers:
        return "blocked"
    if failures:
        return "failed"
    if score >= config.min_score_to_pass:
        return "passed"
    return "not_improved"


def _final_verdict(project_report, diagnoses, score: int, config: RepairLoopConfig):
    if any(item.has_blocker for item in diagnoses) or project_report.score.status == "blocked_by_critical_findings":
        return "blocked"
    if score >= config.min_score_to_pass and not diagnoses:
        return "passed"
    if diagnoses:
        return "failed"
    return "not_improved"
