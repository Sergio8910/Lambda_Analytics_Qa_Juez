"""Modelos para self-heal autonomo de La Colmena."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SelfHealDecision = Literal["kept", "rolled_back", "blocked", "skipped", "failed"]
SelfHealFixType = Literal[
    "python_add_timeout",
    "prompt_add_guardrails",
    "n8n_replace_private_url",
    "manual_review",
]


@dataclass
class SelfHealFixPlan:
    finding_id: str
    finding_title: str
    target_path: str | None
    fix_type: SelfHealFixType
    confidence: float
    reason: str
    before_text: str | None = None
    after_text: str | None = None
    blocked_reason: str | None = None


@dataclass
class SelfHealIteration:
    iteration: int
    finding_id: str
    finding_title: str
    severity: str
    category: str
    target_path: str | None
    confidence: float = 0.0
    fix_type: str | None = None
    decision: SelfHealDecision = "skipped"
    reason: str = ""
    backup_dir: str | None = None
    rollback_audit_path: str | None = None
    score_before: float | None = None
    score_after: float | None = None
    critical_before: int | None = None
    critical_after: int | None = None
    lines_changed: int = 0


@dataclass
class SelfHealResult:
    project_path: str
    started_at: str
    min_confidence: float
    max_iterations: int
    max_lines_per_fix: int
    fast_reeval: bool = False
    score_initial: float | None = None
    score_final: float | None = None
    readiness_initial: str | None = None
    readiness_final: str | None = None
    kept_fixes: int = 0
    rolled_back_fixes: int = 0
    blocked_findings: int = 0
    failed_fixes: int = 0
    human_review_required: list[dict] = field(default_factory=list)
    iterations: list[SelfHealIteration] = field(default_factory=list)
    audit_log_path: str | None = None
    txt_report_path: str | None = None
    json_report_path: str | None = None
