from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


FindingSeverity = Literal["critical", "high", "medium", "low", "info"]
FindingCategory = Literal[
    "structure",
    "logic",
    "redundancy",
    "security",
    "operations",
    "maintainability",
]
NodeCategory = Literal[
    "trigger",
    "webhook",
    "http",
    "code",
    "ai",
    "logic",
    "subworkflow",
    "data",
    "other",
]


class N8nNode(BaseModel):
    node_id: str
    name: str
    node_type: str
    category: NodeCategory
    raw_parameters: Dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False
    has_credentials: bool = False
    credential_keys: List[str] = Field(default_factory=list)
    incoming_edges: int = 0
    outgoing_edges: int = 0
    on_error: Optional[str] = None
    retry_on_fail: bool = False
    continue_on_fail: bool = False

    model_config = {"extra": "forbid"}


class N8nEdge(BaseModel):
    source: str
    target: str
    channel: str = "main"
    output_index: int = 0
    target_input_type: Optional[str] = None

    model_config = {"extra": "forbid"}


class N8nGraph(BaseModel):
    nodes: List[N8nNode] = Field(default_factory=list)
    edges: List[N8nEdge] = Field(default_factory=list)
    trigger_nodes: List[str] = Field(default_factory=list)
    unreachable_nodes: List[str] = Field(default_factory=list)
    disconnected_nodes: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class N8nWorkflowInventory(BaseModel):
    workflow_name: str
    workflow_id: Optional[str] = None
    active: Optional[bool] = None
    total_nodes: int = 0
    total_edges: int = 0
    trigger_nodes: List[str] = Field(default_factory=list)
    webhook_nodes: List[str] = Field(default_factory=list)
    http_nodes: List[str] = Field(default_factory=list)
    ai_nodes: List[str] = Field(default_factory=list)
    code_nodes: List[str] = Field(default_factory=list)
    nodes_with_credentials: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class N8nFinding(BaseModel):
    finding_id: str
    category: FindingCategory
    severity: FindingSeverity
    title: str
    message: str
    node_names: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    recommendation: str

    model_config = {"extra": "forbid"}


class N8nScorecard(BaseModel):
    workflow_integrity: float
    maintainability: float
    security_posture: float
    operational_resilience: float
    redundancy: float
    overall: float
    status: Literal["ok", "warning", "fail"]

    model_config = {"extra": "forbid"}


class N8nDiagnosisFinding(BaseModel):
    finding_id: str
    title: str
    severity: FindingSeverity
    why_it_matters: str
    node_names: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class N8nDiagnosis(BaseModel):
    diagnosis_version: str = "n8n-diagnosis-v1"
    source: Literal["llm", "fallback"]
    model: Optional[str] = None
    verdict: str
    risk_level: Literal["low", "medium", "high", "critical"]
    confidence: Literal["low", "medium", "high"]
    executive_summary: str
    strengths: List[str] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)
    redundancies: List[str] = Field(default_factory=list)
    recommended_actions: List[str] = Field(default_factory=list)
    priority_findings: List[N8nDiagnosisFinding] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class N8nWorkflowAnalysis(BaseModel):
    analysis_version: str = "n8n-static-v1"
    inventory: N8nWorkflowInventory
    scorecard: N8nScorecard
    findings: List[N8nFinding] = Field(default_factory=list)
    counts_by_severity: Dict[str, int] = Field(default_factory=dict)
    counts_by_category: Dict[str, int] = Field(default_factory=dict)
    graph: Optional[N8nGraph] = None
    diagnosis: Optional[N8nDiagnosis] = None

    model_config = {"extra": "forbid"}
