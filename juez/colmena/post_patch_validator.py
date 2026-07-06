"""Reevaluacion posterior a patches aplicados."""
from __future__ import annotations

from pathlib import Path

from .patch_apply_models import PostPatchValidationResult
from .project_evaluator import evaluate_project_path


def validate_after_patch(
    project_path: Path | str,
    *,
    score_before: float | None,
    readiness_before: str | None,
    critical_findings_before: int | None,
) -> PostPatchValidationResult:
    result = PostPatchValidationResult(
        score_before=score_before,
        readiness_before=readiness_before,
        critical_findings_before=critical_findings_before,
    )
    try:
        report = evaluate_project_path(project_path)
    except Exception as exc:
        result.warnings.append(f"No se pudo reevaluar el proyecto: {exc}")
        return result

    result.executed = True
    result.score_after = report.score.score
    result.readiness_after = report.score.status
    result.critical_findings_after = report.score.critical_findings
    if score_before is not None and report.score.score < score_before:
        result.rollback_recommended = True
        result.warnings.append("El score bajo despues de aplicar patches; rollback recomendado.")
    if (
        critical_findings_before is not None
        and report.score.critical_findings > critical_findings_before
    ):
        result.rollback_recommended = True
        result.warnings.append("Aparecieron nuevos criticos; rollback recomendado.")
    return result
