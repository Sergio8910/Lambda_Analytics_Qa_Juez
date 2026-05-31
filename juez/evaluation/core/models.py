from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr


class AgentInfo(BaseModel):
    name: str = "agent"
    version: str = "unknown"
    kind: Literal[
        "chat",
        "rag_chat",
        "tool_agent",
        "structured_generator",
        "classifier",
        "extractor",
        "voice_agent",
    ] = "chat"


class CaseInfo(BaseModel):
    case_id: str
    tags: List[str] = Field(default_factory=list)
    severity: str = "media"


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class InputInfo(BaseModel):
    user_message: str
    system_prompt: str = ""
    conversation: List[ConversationTurn] = Field(default_factory=list)


class ContextInfo(BaseModel):
    provided_context: List[str] = Field(default_factory=list)
    retrieval_context: List[str] = Field(default_factory=list)
    tools_available: List[str] = Field(default_factory=list)


class ExecutionTrace(BaseModel):
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class ExecutionInfo(BaseModel):
    output_text: str = ""
    output_json: Optional[Dict[str, Any]] = None
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    trace: ExecutionTrace = Field(default_factory=ExecutionTrace)


class ContractInfo(BaseModel):
    output_format: Literal["free_text", "json", "tool_calls", "label", "fields"] = "free_text"
    language: str = "es"
    truth_source: Literal["context_only", "context_plus_tools", "open_world_allowed"] = "context_only"
    json_schema: Optional[Dict[str, Any]] = None
    require_clarifying_question_if_ambiguous: bool = False
    must_include: List[str] = Field(default_factory=list)
    must_not_include: List[str] = Field(default_factory=list)


class NormalizedRun(BaseModel):
    run_id: str
    agent: AgentInfo
    case: CaseInfo
    input: InputInfo
    context: ContextInfo
    execution: ExecutionInfo
    contract: ContractInfo
    _error: str | None = PrivateAttr(default=None)

    @property
    def output_text(self) -> str:
        return self.execution.output_text

    @property
    def retrieval_context(self) -> List[str]:
        return self.context.retrieval_context

    @property
    def latency_ms(self) -> float:
        return self.execution.trace.latency_ms

    @property
    def error(self) -> str | None:
        return self._error
