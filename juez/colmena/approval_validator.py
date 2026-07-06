"""Validacion de archivos de aprobacion humana de patches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .approval_models import PatchApprovalItem, PatchApprovalValidationResult
from .safety_gates import can_mark_patch_as_applicable

_VALID_DECISIONS = {"approve", "reject", None}
_REQUIRED_FIELDS = {"proposal_id", "patch_id", "patch_file_path", "checksum"}


def validate_approval_file(approval_file: Path | str) -> PatchApprovalValidationResult:
    approval_path = Path(approval_file)
    result = PatchApprovalValidationResult(approval_file_path=str(approval_path), valid=False)
    if not approval_path.exists():
        result.errors.append(f"Approval file does not exist: {approval_path}")
        return result

    try:
        payload = json.loads(approval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.errors.append(f"Approval file is not valid JSON: {exc}")
        return result

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        result.errors.append("Approval file must contain an items list.")
        return result

    result.total_items = len(items)
    for index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            result.errors.append(f"Item {index} is not an object.")
            result.blocked_items += 1
            continue
        _validate_item(index, raw_item, approval_path, result)

    result.valid = not result.errors
    result.files_modified = 0
    result.fixes_applied = 0
    return result


def _validate_item(
    index: int,
    raw_item: dict[str, Any],
    approval_path: Path,
    result: PatchApprovalValidationResult,
) -> None:
    missing = sorted(field for field in _REQUIRED_FIELDS if not raw_item.get(field))
    if missing:
        result.errors.append(f"Item {index} is missing required fields: {', '.join(missing)}")
        result.blocked_items += 1
        return

    decision = raw_item.get("decision")
    if decision not in _VALID_DECISIONS:
        result.errors.append(f"Item {index} has invalid decision: {decision}")
        result.blocked_items += 1
        return

    patch_path = _resolve_patch_path(str(raw_item["patch_file_path"]), approval_path)
    if patch_path is None or not patch_path.exists():
        result.errors.append(f"Patch file does not exist: {raw_item['patch_file_path']}")
        result.blocked_items += 1
        return

    expected_checksum = str(raw_item["checksum"])
    actual_checksum = _sha256_file(patch_path)
    if expected_checksum != actual_checksum:
        result.errors.append(
            f"Patch checksum mismatch for {raw_item['patch_file_path']}: "
            f"expected {expected_checksum}, got {actual_checksum}"
        )
        result.blocked_items += 1
        return

    item = PatchApprovalItem(
        proposal_id=str(raw_item["proposal_id"]),
        patch_id=str(raw_item["patch_id"]),
        target_path=raw_item.get("target_path"),
        patch_file_path=str(raw_item["patch_file_path"]),
        status=raw_item.get("status") or "pending",
        decision=decision,
        reviewer=raw_item.get("reviewer"),
        reviewed_at=raw_item.get("reviewed_at"),
        reason=raw_item.get("reason"),
        risk=raw_item.get("risk") or "low",
        safe_to_apply=bool(raw_item.get("safe_to_apply")),
        checksum=expected_checksum,
    )

    if decision == "approve":
        allowed, reason = can_mark_patch_as_applicable(item)
        if not allowed:
            result.errors.append(f"Item {index} cannot be approved: {reason}")
            result.blocked_items += 1
            return
        result.approved_items += 1
        return
    if decision == "reject":
        result.rejected_items += 1
        return
    result.pending_items += 1


def _resolve_patch_path(raw_path: str, approval_path: Path) -> Path | None:
    patch_path = Path(raw_path)
    candidates = [patch_path]
    if not patch_path.is_absolute():
        candidates.append(approval_path.parent / patch_path)
        if approval_path.parent.parent != approval_path.parent:
            candidates.append(approval_path.parent.parent / patch_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
