from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AgentRef(BaseModel):
    module: str
    function: str = "run_agent"

    model_config = {"extra": "forbid"}


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    ts: Optional[str] = None

    model_config = {"extra": "forbid"}


class EvaluateRequest(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None
    prompt_base: Optional[str] = None
    metrics: Optional[List[Dict[str, Any]]] = None
    config: Optional[Dict[str, Any]] = None
    cases: Optional[List[Dict[str, Any]]] = None
    mode: Literal["run_agent", "replay"] = "run_agent"
    agent_ref: Optional[AgentRef] = None
    conversation: Optional[List[ConversationTurn]] = None
    retrieval_context: Optional[List[Any]] = None
    n_cases: int = Field(default=30, ge=1, le=50)
    audit_mode: Literal["balanced", "enterprise"] = "balanced"
    return_pdf: bool = False
    seed: Optional[int] = None

    model_config = {"extra": "forbid"}


class EvaluateResponse(BaseModel):
    report: Dict[str, Any]
    pdf_base64: Optional[str] = None

    model_config = {"extra": "forbid"}


class GenerateCasesRequest(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict)
    prompt: Optional[str] = None
    prompt_base: Optional[str] = None
    retrieval_context: Optional[List[Any]] = None
    n_cases: int = Field(default=30, ge=1, le=50)
    seed: Optional[int] = None
    run_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class GenerateCasesResponse(BaseModel):
    cases: List[Dict[str, Any]]
    n_cases: int
    seed: Optional[int] = None

    model_config = {"extra": "forbid"}


class AutogenAgentHttp(BaseModel):
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = 10000

    model_config = {"extra": "forbid"}


class AutogenEvaluateRequest(BaseModel):
    agent_name: str
    prompt_base: str
    n_cases: int = Field(default=30, ge=1, le=50)
    metrics: List[str] = Field(default_factory=list)
    audit_mode: Literal["balanced", "enterprise"] = "balanced"
    seed: Optional[int] = None
    agent_http: AutogenAgentHttp
    rag_id: Optional[str] = None
    return_pdf: bool = False

    model_config = {"extra": "forbid"}


class AutogenEvaluateResponse(BaseModel):
    report: Dict[str, Any]
    pdf_base64: Optional[str] = None

    model_config = {"extra": "forbid"}


class UploadRagResponse(BaseModel):
    rag_id: str
    path: str

    model_config = {"extra": "forbid"}
