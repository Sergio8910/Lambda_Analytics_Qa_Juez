"""Analisis de fallos del repair loop dry-run."""
from __future__ import annotations

from .models import FailureDiagnosis, ProjectEvaluationReport, SyntheticTestResult

_CATEGORY_TO_CAUSE = {
    "missing_documentation": "El proyecto no documenta suficientemente como instalar, ejecutar u operar.",
    "missing_env_example": "No hay plantilla segura de variables de entorno.",
    "missing_tests": "No hay evidencia de pruebas automatizadas.",
    "weak_error_handling": "Faltan timeouts, retry, manejo de errores o degradacion controlada.",
    "weak_prompt_boundaries": "El prompt no declara limites, rol o rechazo de solicitudes inseguras.",
    "n8n_missing_error_branch": "El workflow no evidencia ramas de error/retry suficientes.",
    "security_risk": "Hay riesgos de seguridad bloqueantes o de revision prioritaria.",
    "manual_review_required": "El hallazgo requiere decision humana o contexto de negocio.",
}


def analyze_failures(
    project_report: ProjectEvaluationReport,
    test_results: list[SyntheticTestResult],
) -> list[FailureDiagnosis]:
    diagnoses: list[FailureDiagnosis] = []
    seq = 1
    for result in test_results:
        if result.passed:
            continue
        for finding in result.findings or [{"category": "unknown", "severity": "medium", "message": result.message}]:
            category = str(finding.get("category") or "unknown")
            severity = _normalize_severity(str(finding.get("severity") or "medium"))
            diagnoses.append(
                FailureDiagnosis(
                    id=f"DIAG-{seq:03d}",
                    severity=severity,
                    category=category,
                    message=str(finding.get("message") or result.message),
                    probable_cause=_CATEGORY_TO_CAUSE.get(category, "No hay suficiente evidencia para una causa unica."),
                    evidence=[result.case_id, *result.evidence],
                    has_blocker=severity == "critical",
                )
            )
            seq += 1

    for finding in project_report.findings:
        if finding.severity not in {"critical", "high"}:
            continue
        category = "security_risk" if finding.category == "security" else "manual_review_required"
        diagnoses.append(
            FailureDiagnosis(
                id=f"DIAG-{seq:03d}",
                severity=finding.severity,
                category=category,
                message=finding.title,
                probable_cause=finding.description,
                evidence=[v for v in [finding.file or "", finding.evidence] if v],
                has_blocker=finding.severity == "critical",
            )
        )
        seq += 1
    return _dedupe(diagnoses)


def _normalize_severity(value: str):
    return {
        "critical": "critical",
        "critico": "critical",
        "high": "high",
        "alto": "high",
        "medium": "medium",
        "medio": "medium",
        "low": "low",
        "bajo": "low",
        "info": "info",
    }.get(value.lower().strip(), "medium")


def _dedupe(items: list[FailureDiagnosis]) -> list[FailureDiagnosis]:
    seen: set[tuple[str, str, str]] = set()
    out: list[FailureDiagnosis] = []
    for item in items:
        key = (item.severity, item.category, item.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return [item.model_copy(update={"id": f"DIAG-{idx:03d}"}) for idx, item in enumerate(out, start=1)]
