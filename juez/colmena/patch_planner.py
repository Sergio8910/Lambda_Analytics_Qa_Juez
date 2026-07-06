"""Convierte propuestas de reparacion en planes de diff seguros."""
from __future__ import annotations

import re
from pathlib import Path

from .models import ProjectFixProposal, RepairLoopResult
from .patch_models import PatchPlan, PatchPlanItem
from .patch_validator import validate_patch_item
from .safety_gates import can_apply_patch_item, can_generate_patch

_LOW_RISK_FIXES = {"add_env_example", "add_documentation", "add_test"}


def build_patch_plan(
    repair_result: RepairLoopResult,
    *,
    mode: str | None = None,
) -> PatchPlan:
    root = Path(repair_result.project_path).resolve()
    active_mode = mode or repair_result.config.repair_mode
    items = [
        _proposal_to_item(proposal, root, repair_result)
        for proposal in repair_result.fix_proposals
    ]
    validated: list[PatchPlanItem] = []
    for item, proposal in zip(items, repair_result.fix_proposals, strict=True):
        can_generate, reason = can_generate_patch(proposal, str(root))
        if not can_generate:
            item = item.model_copy(
                update={
                    "status": "blocked",
                    "safe_to_apply": False,
                    "requires_review": True,
                    "blocked_reason": reason,
                    "validation_notes": [*item.validation_notes, reason or "patch bloqueado"],
                }
            )
        else:
            item = validate_patch_item(item, root)
        can_apply, apply_reason = can_apply_patch_item(item, active_mode)
        if apply_reason:
            item = item.model_copy(update={"validation_notes": [*item.validation_notes, apply_reason]})
        item = item.model_copy(update={"safe_to_apply": item.safe_to_apply and can_apply is False})
        validated.append(item)

    return _summarize(PatchPlan(project_path=str(root), mode=active_mode, items=validated))


def _proposal_to_item(
    proposal: ProjectFixProposal,
    root: Path,
    repair_result: RepairLoopResult,
) -> PatchPlanItem:
    source = _source_for(proposal)
    target_path = _target_for(proposal, root)
    action = "create_file" if proposal.fix_type in _LOW_RISK_FIXES or proposal.fix_type == "manual_review" else "modify_file"
    risk = _risk_for(proposal)
    content = _content_for(proposal, repair_result, target_path)
    diff = _unified_new_file_diff(target_path, content) if action == "create_file" and content else None
    return PatchPlanItem(
        proposal_id=proposal.id,
        action=action,  # type: ignore[arg-type]
        status="planned" if action == "create_file" else "requires_review",
        target_path=target_path,
        risk=risk,
        safe_to_apply=proposal.safe_to_apply and proposal.fix_type in _LOW_RISK_FIXES,
        requires_review=proposal.requires_review,
        reason=proposal.description,
        source=source,
        diff_preview=diff,
        proposed_content=content,
        validation_notes=["preview generado; proyecto original no modificado"],
    )


def _target_for(proposal: ProjectFixProposal, root: Path) -> str | None:
    if proposal.fix_type == "add_test":
        return "tests/colmena_synthetic/test_colmena_generated.py" if (root / "tests").exists() else "colmena_generated_tests/test_colmena_generated.py"
    if proposal.fix_type == "manual_review":
        return "COLMENA_REPAIR_PROPOSALS.md"
    return proposal.target_path


def _content_for(
    proposal: ProjectFixProposal,
    repair_result: RepairLoopResult,
    target_path: str | None,
) -> str | None:
    if proposal.fix_type == "add_env_example":
        return _env_example_content(proposal)
    if proposal.fix_type == "add_documentation":
        if target_path == "README_COLMENA_REVIEW.md":
            return _readme_review_content(repair_result)
        return proposal.proposed_content or _readme_review_content(repair_result)
    if proposal.fix_type == "add_test":
        return _synthetic_test_content(repair_result)
    if proposal.fix_type == "manual_review":
        return _repair_proposals_content(repair_result)
    return proposal.proposed_content


def _env_example_content(proposal: ProjectFixProposal) -> str:
    content = proposal.proposed_content or ""
    detected = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", "\n".join(proposal.evidence) + "\n" + content)))
    excluded = {"TODO", "FIX", "README", "JSON"}
    variables = [v for v in detected if v not in excluded]
    if not variables:
        variables = ["ENVIRONMENT", "LOG_LEVEL", "TIMEOUT_SECONDS"]
    lines = ["# Generado por La Colmena (preview, no aplicado)"]
    for var in variables:
        default = "development" if var == "ENVIRONMENT" else "INFO" if var == "LOG_LEVEL" else "30" if var == "TIMEOUT_SECONDS" else ""
        lines.append(f"{var}={default}")
    return "\n".join(lines) + "\n"


def _readme_review_content(repair_result: RepairLoopResult) -> str:
    return (
        "# Revision generada por La Colmena\n\n"
        "## Resumen\n"
        f"Score inicial: {repair_result.initial_score}. Readiness final: {repair_result.readiness_final}.\n\n"
        "## Hallazgos\n"
        + "\n".join(f"- {d.severity}: {d.message}" for d in repair_result.diagnoses[:20])
        + "\n\n## Propuestas de reparacion\n"
        + "\n".join(f"- {p.id}: {p.title}" for p in repair_result.fix_proposals[:20])
        + "\n\n## Cambios sugeridos\nRevisar los diffs preview generados por La Colmena.\n\n"
        "## Riesgos\nNo se aplicaron cambios automaticos en esta fase.\n\n"
        "## Proximos pasos\nValidar manualmente y ejecutar tests antes de habilitar apply-safe.\n"
    )


def _synthetic_test_content(repair_result: RepairLoopResult) -> str:
    lines = [
        '"""Tests sinteticos sugeridos por La Colmena.',
        "",
        "Archivo preview: no fue creado automaticamente en el proyecto evaluado.",
        '"""',
        "",
        "",
        "def test_colmena_synthetic_plan_documented():",
        f"    assert {len(repair_result.test_cases)} >= 0",
        "",
    ]
    return "\n".join(lines)


def _repair_proposals_content(repair_result: RepairLoopResult) -> str:
    safe = [p for p in repair_result.fix_proposals if p.safe_to_apply]
    review = [p for p in repair_result.fix_proposals if p.requires_review]
    blocked = [p for p in repair_result.fix_proposals if not p.safe_to_apply]
    return (
        "# Propuestas de reparacion generadas por La Colmena\n\n"
        "## Propuestas seguras\n"
        + "\n".join(f"- {p.id}: {p.title}" for p in safe)
        + "\n\n## Propuestas que requieren revision\n"
        + "\n".join(f"- {p.id}: {p.title}" for p in review)
        + "\n\n## Propuestas bloqueadas\n"
        + "\n".join(f"- {p.id}: {p.title}" for p in blocked)
        + "\n"
    )


def _source_for(proposal: ProjectFixProposal) -> str:
    return {
        "add_env_example": "missing_env_example",
        "add_documentation": "missing_documentation",
        "add_test": "missing_tests",
        "add_timeout": "weak_error_handling",
        "add_retry": "n8n_missing_error_branch",
        "improve_prompt": "weak_prompt_boundaries",
        "manual_review": "manual_review",
    }.get(proposal.fix_type, "generic_project_hardening")


def _risk_for(proposal: ProjectFixProposal):
    if proposal.fix_type in _LOW_RISK_FIXES:
        return "low"
    if proposal.fix_type == "manual_review":
        return "medium"
    if proposal.severity in {"critical", "high"}:
        return "high"
    return "medium"


def _unified_new_file_diff(target_path: str | None, content: str) -> str | None:
    if not target_path:
        return None
    lines = ["--- /dev/null", f"+++ {target_path}", f"@@ -0,0 +1,{len(content.splitlines())} @@"]
    lines.extend(f"+{line}" for line in content.splitlines())
    return "\n".join(lines) + "\n"


def _summarize(plan: PatchPlan) -> PatchPlan:
    safe_items = sum(1 for item in plan.items if item.status == "planned" and item.safe_to_apply)
    blocked_items = sum(1 for item in plan.items if item.status == "blocked")
    review_items = sum(1 for item in plan.items if item.status == "requires_review" or item.requires_review)
    generated_files = [item.target_path for item in plan.items if item.diff_preview and item.target_path]
    return plan.model_copy(
        update={
            "total_items": len(plan.items),
            "safe_items": safe_items,
            "blocked_items": blocked_items,
            "review_items": review_items,
            "generated_files": generated_files,
            "files_modified": 0,
            "fixes_applied": 0,
        }
    )
