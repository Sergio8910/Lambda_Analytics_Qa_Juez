from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PromptProfile(BaseModel):
    language: Literal["es", "en", "unknown"] = "unknown"
    requires_json: bool = False
    forbids_markdown: bool = False
    context_dependency: bool = False
    output_format_hint: Optional[str] = None
    strictness: Literal["low", "med", "high"] = "med"
    domain: str = "general"
    keywords: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class AutoGenRequest(BaseModel):
    prompt_base: str
    metrics: List[Dict[str, Any]] = Field(default_factory=list)
    n_cases: int = Field(default=30, ge=1, le=50)
    seed: Optional[int] = None
    run_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class AutoGenSummary(BaseModel):
    n_cases: int
    seed: int
    distribution_counts: Dict[str, int] = Field(default_factory=dict)
    context_stats: Dict[str, Any] = Field(default_factory=dict)
    failures_by_tag: Dict[str, int] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}
