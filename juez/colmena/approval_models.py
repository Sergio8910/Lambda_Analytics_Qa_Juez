"""Modelos para aprobacion humana de patches de La Colmena."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ApprovalStatus = Literal["pending", "approved", "rejected", "blocked"]
ApprovalDecision = Literal["approve", "reject"]
PatchExportStatus = Literal["exported", "skipped", "blocked"]


@dataclass
class ExportedPatchFile:
    proposal_id: str
    patch_id: str
    target_path: str | None
    patch_file_path: str | None
    status: PatchExportStatus
    reason: str
    risk: str
    safe_to_apply: bool
    checksum: str | None = None


@dataclass
class PatchExportResult:
    project_path: str
    generated_at: str
    output_dir: str
    patches_dir: str
    items: list[ExportedPatchFile] = field(default_factory=list)
    total_items: int = 0
    exported_items: int = 0
    skipped_items: int = 0
    blocked_items: int = 0
    files_modified: int = 0
    fixes_applied: int = 0


@dataclass
class PatchApprovalItem:
    proposal_id: str
    patch_id: str
    target_path: str | None
    patch_file_path: str | None
    status: ApprovalStatus = "pending"
    decision: ApprovalDecision | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    reason: str | None = None
    risk: str = "low"
    safe_to_apply: bool = False
    checksum: str | None = None


@dataclass
class PatchApprovalManifest:
    project_path: str
    generated_at: str
    approval_required: bool = True
    instructions: str = (
        "Cambie decision a approve o reject. No modifique proposal_id, patch_id, "
        "patch_file_path ni checksum. Nada se aplica en esta fase."
    )
    items: list[PatchApprovalItem] = field(default_factory=list)
    total_items: int = 0
    pending_items: int = 0
    approved_items: int = 0
    rejected_items: int = 0
    blocked_items: int = 0
    manifest_path: str | None = None
    files_modified: int = 0
    fixes_applied: int = 0


@dataclass
class PatchApprovalValidationResult:
    approval_file_path: str
    valid: bool
    approved_items: int = 0
    rejected_items: int = 0
    pending_items: int = 0
    blocked_items: int = 0
    total_items: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_modified: int = 0
    fixes_applied: int = 0


@dataclass
class PatchApprovalReport:
    project_path: str
    repair_mode: str = "dry-run"
    generate_diffs: bool = False
    export_patches: bool = False
    total_patch_items: int = 0
    patches_exported: int = 0
    patches_blocked: int = 0
    patches_skipped: int = 0
    pending_patches: int = 0
    approved_patches: int = 0
    rejected_patches: int = 0
    blocked_patches: int = 0
    manifest_generated: bool = False
    manifest_path: str | None = None
    approval_file_provided: bool = False
    approval_file_path: str | None = None
    approval_file_valid: bool | None = None
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    exported_files: list[str] = field(default_factory=list)
    txt_report_path: str | None = None
    json_report_path: str | None = None
    files_modified: int = 0
    fixes_applied: int = 0


def to_dict(value: object) -> dict:
    return asdict(value)
