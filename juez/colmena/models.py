"""Modelos de La Colmena para evaluacion de proyectos completos.

Estos modelos son aditivos: no reemplazan ``Componente`` ni ``ColmenaResult``,
que siguen siendo la capa historica de evaluacion de agentes/flujos.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
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
