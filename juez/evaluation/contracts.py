from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


AgentKind = Literal[
    "chat",
    "rag_chat",
    "tool_agent",
    "structured_generator",
    "classifier",
    "extractor",
    "voice_agent",
]


FinishReason = Literal[
    "stop",
    "length",
    "tool_call",
    "error",
    "unknown",
]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Optional[Dict[str, Any]] = None
    call_id: Optional[str] = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AgentEnvelope:
    output_text: str
    retrieval_context: List[str]
    output_json: Optional[Dict[str, Any]] = None
    labels: Optional[Dict[str, Any]] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    agent_kind: Optional[AgentKind] = None
    finish_reason: Optional[FinishReason] = None
    latency_ms: Optional[float] = None
    model: Optional[str] = None
    usage: Optional[Usage] = None
    raw: Optional[Any] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunnerResult:
    output_text: str
    retrieval_context: list[str]
    latency_ms: float
    error: Optional[str] = None
    envelope: Optional[AgentEnvelope] = None
