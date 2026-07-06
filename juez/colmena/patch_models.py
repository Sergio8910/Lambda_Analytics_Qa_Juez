"""Modelos para planes de patch seguros en La Colmena."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PatchAction = Literal["create_file", "modify_file", "skip"]
PatchRisk = Literal["low", "medium", "high", "critical"]
PatchStatus = Literal["planned", "blocked", "requires_review", "not_applicable"]
PatchSource = Literal[
    "missing_env_example",
    "missing_readme",
    "missing_tests",
    "missing_documentation",
    "weak_error_handling",
    "weak_prompt_boundaries",
    "n8n_missing_error_branch",
    "generic_project_hardening",
    "manual_review",
]


class PatchPlanItem(BaseModel):
    proposal_id: str
    action: PatchAction
    status: PatchStatus
    target_path: str | None = None
    risk: PatchRisk
    safe_to_apply: bool
    requires_review: bool
    reason: str
    source: PatchSource | str
    diff_preview: str | None = None
    proposed_content: str | None = None
    validation_notes: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None

    model_config = {"extra": "forbid"}


class PatchPlan(BaseModel):
    project_path: str
    mode: str = "dry-run"
    generate_diffs: bool = True
    items: list[PatchPlanItem] = Field(default_factory=list)
    total_items: int = 0
    safe_items: int = 0
    blocked_items: int = 0
    review_items: int = 0
    generated_files: list[str] = Field(default_factory=list)
    files_modified: int = 0
    fixes_applied: int = 0
    txt_report_path: str | None = None
    json_report_path: str | None = None

    model_config = {"extra": "forbid"}
