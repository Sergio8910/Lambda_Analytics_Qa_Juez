"""Aplicacion conservadora de patches aprobados por humanos."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .approval_models import PatchApprovalItem
from .approval_validator import validate_approval_file
from .backup_manager import create_apply_backup_manifest
from .patch_apply_models import (
    PatchApplyAuditEntry,
    PatchApplyItemResult,
    PatchApplyResult,
)
from .patch_parser import PatchParseError, parse_new_file_patch
from .post_patch_validator import validate_after_patch
from .project_evaluator import evaluate_project_path
from .safety_gates import can_apply_approved_patch


def apply_approved_patches(
    *,
    project_path: Path | str,
    approval_file: Path | str,
    output_dir: Path | str = "outputs",
) -> PatchApplyResult:
    root = Path(project_path).resolve()
    approval_path = Path(approval_file)
    validation = validate_approval_file(approval_path)
    result = PatchApplyResult(
        project_path=str(root),
        approval_file_path=str(approval_path),
        approval_valid=validation.valid,
        approved_items=validation.approved_items,
        rejected_items=validation.rejected_items,
        pending_items=validation.pending_items,
        blocked_items=validation.blocked_items,
        warnings=list(validation.warnings),
    )

    before_report = _safe_evaluate(root, result)
    if before_report is not None:
        result.score_before = before_report.score.score
        result.readiness_before = before_report.score.status

    if not validation.valid:
        result.warnings.extend(validation.errors)
        result.item_results.append(
            PatchApplyItemResult(
                proposal_id="approval-file",
                patch_id="approval-file",
                target_path=None,
                patch_file_path=str(approval_path),
                status="blocked",
                message="Approval file invalido; no se aplica ningun patch.",
                skipped_reason="; ".join(validation.errors),
            )
        )
        _write_outputs(result, output_dir)
        return result

    payload = _read_json(approval_path)
    raw_items = payload.get("items", []) if isinstance(payload, dict) else []
    approval_items = [_approval_item_from_raw(raw) for raw in raw_items if isinstance(raw, dict)]
    approved_items = [item for item in approval_items if item.decision == "approve"]
    target_paths = [item.target_path for item in approved_items if item.target_path]
    if approved_items:
        result.backup_path = str(
            create_apply_backup_manifest(
                project_path=root,
                target_paths=target_paths,
                output_dir=output_dir,
            )
        )

    for item in approval_items:
        item_result = _apply_one(root, item)
        result.item_results.append(item_result)
        result.audit_entries.append(_audit_from_item(item, item_result))

    _summarize_items(result)
    post = validate_after_patch(
        root,
        score_before=result.score_before,
        readiness_before=result.readiness_before,
        critical_findings_before=before_report.score.critical_findings if before_report is not None else None,
    )
    result.post_evaluation_executed = post.executed
    result.score_after = post.score_after
    result.readiness_after = post.readiness_after
    result.warnings.extend(post.warnings)
    if post.rollback_recommended:
        result.rollback_status = "recommended"
        result.rollback_reason = "Post-evaluation detecto regresion; rollback manual recomendado."

    _write_outputs(result, output_dir)
    return result


def _apply_one(root: Path, item: PatchApprovalItem) -> PatchApplyItemResult:
    if item.decision == "reject":
        return PatchApplyItemResult(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=item.target_path,
            patch_file_path=item.patch_file_path,
            status="skipped",
            message="Patch rechazado por el reviewer.",
            skipped_reason="decision=reject",
        )
    if item.decision is None:
        return PatchApplyItemResult(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=item.target_path,
            patch_file_path=item.patch_file_path,
            status="skipped",
            message="Patch pendiente de aprobacion.",
            skipped_reason="decision=pending",
        )
    if item.decision != "approve":
        return PatchApplyItemResult(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=item.target_path,
            patch_file_path=item.patch_file_path,
            status="blocked",
            message=f"Decision no soportada: {item.decision}",
            skipped_reason="decision invalida",
        )

    try:
        parsed = parse_new_file_patch(
            patch_id=item.patch_id,
            proposal_id=item.proposal_id,
            patch_file_path=item.patch_file_path or "",
        )
        allowed, reason = can_apply_approved_patch(item, parsed, str(root))
        if not allowed:
            return PatchApplyItemResult(
                proposal_id=item.proposal_id,
                patch_id=item.patch_id,
                target_path=parsed.target_path,
                patch_file_path=item.patch_file_path,
                status="blocked",
                action=parsed.action,
                message=reason or "Patch bloqueado por safety gate.",
                skipped_reason=reason,
            )
        target = root / parsed.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(parsed.content, encoding="utf-8", newline="\n")
        return PatchApplyItemResult(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=parsed.target_path,
            patch_file_path=item.patch_file_path,
            status="applied",
            action=parsed.action,
            message="Archivo nuevo creado desde patch aprobado.",
            created_file=True,
            modified_file=False,
        )
    except PatchParseError as exc:
        return PatchApplyItemResult(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=item.target_path,
            patch_file_path=item.patch_file_path,
            status="blocked",
            message="Patch no compatible con create_file_only.",
            skipped_reason=str(exc),
        )
    except Exception as exc:
        return PatchApplyItemResult(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=item.target_path,
            patch_file_path=item.patch_file_path,
            status="failed",
            message="Fallo al aplicar patch.",
            error=str(exc),
        )


def _summarize_items(result: PatchApplyResult) -> None:
    result.applied_items = sum(1 for item in result.item_results if item.status == "applied")
    result.blocked_items = sum(1 for item in result.item_results if item.status == "blocked")
    result.skipped_items = sum(1 for item in result.item_results if item.status == "skipped")
    result.failed_items = sum(1 for item in result.item_results if item.status == "failed")
    result.applicable_items = sum(
        1 for item in result.item_results if item.status in {"applied", "blocked", "failed"}
    )
    result.files_created = sum(1 for item in result.item_results if item.created_file)
    result.files_modified = sum(1 for item in result.item_results if item.modified_file)
    result.files_overwritten = 0


def _audit_from_item(item: PatchApprovalItem, result: PatchApplyItemResult) -> PatchApplyAuditEntry:
    return PatchApplyAuditEntry(
        proposal_id=item.proposal_id,
        patch_id=item.patch_id,
        target_path=result.target_path,
        patch_file_path=item.patch_file_path,
        decision=item.decision,
        status=result.status,
        message=result.error or result.skipped_reason or result.message,
        timestamp=datetime.now(UTC).isoformat(),
        created_file=result.created_file,
        modified_file=result.modified_file,
    )


def _approval_item_from_raw(raw: dict[str, Any]) -> PatchApprovalItem:
    return PatchApprovalItem(
        proposal_id=str(raw.get("proposal_id") or ""),
        patch_id=str(raw.get("patch_id") or ""),
        target_path=raw.get("target_path"),
        patch_file_path=raw.get("patch_file_path"),
        status=raw.get("status") or "pending",
        decision=raw.get("decision"),
        reviewer=raw.get("reviewer"),
        reviewed_at=raw.get("reviewed_at"),
        reason=raw.get("reason"),
        risk=raw.get("risk") or "low",
        safe_to_apply=bool(raw.get("safe_to_apply")),
        checksum=raw.get("checksum"),
    )


def _safe_evaluate(root: Path, result: PatchApplyResult):
    try:
        return evaluate_project_path(root)
    except Exception as exc:
        result.warnings.append(f"No se pudo evaluar antes de aplicar patches: {exc}")
        return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_outputs(result: PatchApplyResult, output_dir: Path | str) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    audit = out / f"colmena_patch_apply_audit_{stamp}.json"
    audit.write_text(
        json.dumps(
            {
                "project_path": result.project_path,
                "approval_file_path": result.approval_file_path,
                "entries": [asdict(entry) for entry in result.audit_entries],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result.audit_log_path = str(audit)
