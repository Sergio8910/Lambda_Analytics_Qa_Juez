"""Modelos de La Colmena para evaluacion de proyectos completos.

Estos modelos son aditivos: no reemplazan ``Componente`` ni ``ColmenaResult``,
que siguen siendo la capa historica de evaluacion de agentes/flujos.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
RepairMode = Literal["dry-run", "proposal-only", "apply-safe"]
RepairVerdict = Literal["passed", "failed", "blocked", "improved", "not_improved"]
CaseType = Literal[
    "n8n_workflow",
    "eleven_voice_agent",
    "prompt",
    "artifact",
    "config",
    "api",
    "generic_project",
]
FixType = Literal[
    "create_file",
    "modify_file",
    "add_config",
    "add_test",
    "add_documentation",
    "add_env_example",
    "add_timeout",
    "add_retry",
    "add_validation",
    "improve_prompt",
    "manual_review",
]
ReadinessStatus = Literal[
    "ready_for_production",
    "ready_with_observations",
    "not_ready_for_production",
    "blocked_by_critical_findings",
    "unknown",
]
ProjectType = Literal[
    "agent_only",
    "multiagent_project",
    "python_api_project",
    "fastapi_project",
    "mcp_server",
    "n8n_workflow_project",
    "automation_project",
    "frontend_backend_project",
    "mixed_ai_project",
    "unknown_project",
]
FindingCategory = Literal[
    "security",
    "api",
    "workflow",
    "agent",
    "prompt",
    "architecture",
    "documentation",
    "deployment",
    "testing",
    "integration",
    "performance",
    "maintainability",
]


class ProjectAsset(BaseModel):
    kind: str
    path: str
    name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ProjectInventory(BaseModel):
    root_path: str
    detected_assets: dict[str, int] = Field(default_factory=dict)
    assets: list[ProjectAsset] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    env_files: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ProjectClassification(BaseModel):
    project_type: ProjectType
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class NormalizedFinding(BaseModel):
    id: str
    severity: Severity
    category: FindingCategory
    title: str
    description: str
    file: str | None = None
    line: int | None = None
    evidence: str = ""
    impact: str = ""
    recommendation: str = ""
    auto_fix_available: bool = False
    source: str

    model_config = {"extra": "forbid"}


class ProjectScore(BaseModel):
    score: float
    status: ReadinessStatus
    blocking_findings: int = 0
    critical_findings: int = 0
    high_findings: int = 0
    medium_findings: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    weighted_penalty: float = 0.0

    model_config = {"extra": "forbid"}


class ProjectEvaluationReport(BaseModel):
    project_id: str
    root_path: str
    inventory: ProjectInventory
    classification: ProjectClassification
    findings: list[NormalizedFinding] = Field(default_factory=list)
    score: ProjectScore
    recommendations: list[str] = Field(default_factory=list)
    auto_fixable: list[str] = Field(default_factory=list)
    human_review_required: list[str] = Field(default_factory=list)
    legacy_component_score: float | None = None
    legacy_component_findings: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class RepairLoopConfig(BaseModel):
    cases_count: int = Field(default=10, ge=1, le=100)
    max_iterations: int = Field(default=3, ge=1, le=20)
    repair_mode: RepairMode = "dry-run"
    min_score_to_pass: int = Field(default=85, ge=0, le=100)
    stop_on_blocker: bool = True

    model_config = {"extra": "forbid"}


class SyntheticTestCase(BaseModel):
    id: str
    title: str
    case_type: CaseType
    target_path: str | None = None
    description: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: str | None = None
    should_execute_real_flow: bool = False

    model_config = {"extra": "forbid"}


class SyntheticTestResult(BaseModel):
    case_id: str
    passed: bool
    score: int = Field(ge=0, le=100)
    message: str
    evidence: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class FailureDiagnosis(BaseModel):
    id: str
    severity: Severity
    category: str
    message: str
    probable_cause: str
    evidence: list[str] = Field(default_factory=list)
    has_blocker: bool = False

    model_config = {"extra": "forbid"}


class ProjectFixProposal(BaseModel):
    id: str
    title: str
    description: str
    severity: Severity
    fix_type: FixType
    target_path: str | None = None
    safe_to_apply: bool = False
    requires_review: bool = True
    evidence: list[str] = Field(default_factory=list)
    proposed_content: str | None = None
    applied: bool = False
    skipped_reason: str | None = None

    model_config = {"extra": "forbid"}


class RepairIterationResult(BaseModel):
    iteration: int
    score_before: int | None = None
    score_after: int | None = None
    verdict: RepairVerdict
    test_cases_generated: int = 0
    test_cases_executed: int = 0
    failures_found: int = 0
    fixes_proposed: int = 0
    fixes_applied: int = 0
    blockers_found: int = 0
    report_path: str | None = None
    notes: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class RepairLoopResult(BaseModel):
    project_path: str
    config: RepairLoopConfig
    initial_score: int | None = None
    final_score: int | None = None
    final_verdict: RepairVerdict
    readiness_final: ReadinessStatus | None = None
    iterations: list[RepairIterationResult] = Field(default_factory=list)
    test_cases: list[SyntheticTestCase] = Field(default_factory=list)
    test_results: list[SyntheticTestResult] = Field(default_factory=list)
    diagnoses: list[FailureDiagnosis] = Field(default_factory=list)
    fix_proposals: list[ProjectFixProposal] = Field(default_factory=list)
    txt_report_path: str | None = None
    json_report_path: str | None = None

    model_config = {"extra": "forbid"}
