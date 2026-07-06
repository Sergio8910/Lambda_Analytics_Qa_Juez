"""Planificador conservador de propuestas de reparacion."""
from __future__ import annotations

from .models import FailureDiagnosis, ProjectFixProposal
from .safety_gates import can_apply_fix


def plan_fixes(diagnoses: list[FailureDiagnosis], mode: str = "dry-run") -> list[ProjectFixProposal]:
    proposals: list[ProjectFixProposal] = []
    for idx, diagnosis in enumerate(diagnoses, start=1):
        proposal = _proposal_for(idx, diagnosis)
        can_apply, reason = can_apply_fix(proposal, mode)
        proposals.append(
            proposal.model_copy(
                update={
                    "applied": False,
                    "skipped_reason": reason if not can_apply else "No se aplica en esta fase; revision humana requerida.",
                }
            )
        )
    return proposals


def _proposal_for(idx: int, diagnosis: FailureDiagnosis) -> ProjectFixProposal:
    category = diagnosis.category
    if category == "missing_env_example":
        return ProjectFixProposal(
            id=f"FIX-{idx:03d}",
            title="Crear .env.example",
            description="Agregar plantilla de variables de entorno con valores dummy y sin secretos reales.",
            severity=diagnosis.severity,
            fix_type="add_env_example",
            target_path=".env.example",
            safe_to_apply=True,
            requires_review=True,
            evidence=diagnosis.evidence,
            proposed_content="# Variables requeridas\nOPENAI_API_KEY=\nJUDGE_API_KEY=\n",
        )
    if category == "missing_documentation":
        return ProjectFixProposal(
            id=f"FIX-{idx:03d}",
            title="Agregar documentacion operativa minima",
            description="Crear o completar README/README_COLMENA_REVIEW.md con instalacion, ejecucion, env y tests.",
            severity=diagnosis.severity,
            fix_type="add_documentation",
            target_path="README_COLMENA_REVIEW.md",
            safe_to_apply=True,
            requires_review=True,
            evidence=diagnosis.evidence,
            proposed_content="# Revision Colmena\n\nPendiente completar instalacion, ejecucion, variables y tests.\n",
        )
    if category == "missing_tests":
        return ProjectFixProposal(
            id=f"FIX-{idx:03d}",
            title="Agregar smoke tests sinteticos",
            description="Crear pruebas basicas que validen imports, healthchecks o endpoints sin tocar produccion.",
            severity=diagnosis.severity,
            fix_type="add_test",
            target_path="tests/test_colmena_smoke.py",
            safe_to_apply=True,
            requires_review=True,
            evidence=diagnosis.evidence,
            proposed_content="def test_smoke_placeholder():\n    assert True\n",
        )
    if category == "weak_error_handling":
        return ProjectFixProposal(
            id=f"FIX-{idx:03d}",
            title="Agregar timeout/retry y manejo de errores",
            description="Incorporar timeouts explicitos, excepciones controladas y retry donde aplique.",
            severity=diagnosis.severity,
            fix_type="add_timeout",
            safe_to_apply=False,
            requires_review=True,
            evidence=diagnosis.evidence,
        )
    if category == "weak_prompt_boundaries":
        return ProjectFixProposal(
            id=f"FIX-{idx:03d}",
            title="Fortalecer guardrails del prompt",
            description="Agregar limites de rol, rechazo seguro, no exfiltracion e instrucciones ante ambiguedad.",
            severity=diagnosis.severity,
            fix_type="improve_prompt",
            safe_to_apply=False,
            requires_review=True,
            evidence=diagnosis.evidence,
        )
    if category == "n8n_missing_error_branch":
        return ProjectFixProposal(
            id=f"FIX-{idx:03d}",
            title="Agregar rama de error/retry en workflow n8n",
            description="Definir onError/retryOnFail/continueOnFail segun criticidad del nodo.",
            severity=diagnosis.severity,
            fix_type="add_retry",
            safe_to_apply=False,
            requires_review=True,
            evidence=diagnosis.evidence,
        )
    return ProjectFixProposal(
        id=f"FIX-{idx:03d}",
        title="Revision manual prioritaria",
        description=diagnosis.probable_cause,
        severity=diagnosis.severity,
        fix_type="manual_review",
        safe_to_apply=False,
        requires_review=True,
        evidence=diagnosis.evidence,
    )
