from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class EvaluateRequest(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict)
    cases: List[Dict[str, Any]] = Field(default_factory=list)
    audit_mode: Optional[Literal["standard", "enterprise"]] = None

    model_config = {"extra": "forbid"}


class EvaluateResponse(BaseModel):
    summary: Dict[str, Any]
    cases: List[Dict[str, Any]]

    model_config = {"extra": "forbid"}
