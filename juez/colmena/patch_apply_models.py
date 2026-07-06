"""Modelos para aplicacion segura de patches aprobados."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PatchApplyStatus = Literal["applied", "blocked", "skipped", "failed"]
PatchApplyAction = Literal["create_file"]
RollbackStatus = Literal["not_needed", "recommended", "executed", "failed"]


@dataclass
class ParsedPatch:
    patch_id: str
    proposal_id: str
    patch_file_path: str
    target_path: str
    action: PatchApplyAction
    content: str
    checksum: str | None = None


@dataclass
class PatchApplyItemResult:
    proposal_id: str
    patch_id: str
    target_path: str | None
    patch_file_path: str | None
    status: PatchApplyStatus
    action: str | None = None
    message: str = ""
    created_file: bool = False
    modified_file: bool = False
    skipped_reason: str | None = None
    error: str | None = None


@dataclass
class PatchApplyAuditEntry:
    proposal_id: str
    patch_id: str
    target_path: str | None
    patch_file_path: str | None
    decision: str | None
    status: PatchApplyStatus
    message: str
    timestamp: str
    created_file: bool = False
    modified_file: bool = False


@dataclass
class PostPatchValidationResult:
    executed: bool = False
    score_before: float | None = None
    score_after: float | None = None
    readiness_before: str | None = None
    readiness_after: str | None = None
    critical_findings_before: int | None = None
    critical_findings_after: int | None = None
    rollback_recommended: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class PatchApplyResult:
    project_path: str
    approval_file_path: str
    approval_valid: bool
    apply_approved_patches: bool = True
    backup_path: str | None = None
    score_before: float | None = None
    score_after: float | None = None
    readiness_before: str | None = None
    readiness_after: str | None = None
    approved_items: int = 0
    rejected_items: int = 0
    pending_items: int = 0
    applicable_items: int = 0
    applied_items: int = 0
    blocked_items: int = 0
    skipped_items: int = 0
    failed_items: int = 0
    files_created: int = 0
    files_modified: int = 0
    files_overwritten: int = 0
    post_evaluation_executed: bool = False
    rollback_status: RollbackStatus = "not_needed"
    rollback_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    item_results: list[PatchApplyItemResult] = field(default_factory=list)
    audit_entries: list[PatchApplyAuditEntry] = field(default_factory=list)
    txt_report_path: str | None = None
    json_report_path: str | None = None
    audit_log_path: str | None = None
