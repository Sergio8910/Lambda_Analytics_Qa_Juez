from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AgentTypePolicy:
    agent_type: str
    required_dimensions: List[str]
    relevant_metrics: List[str]
    notes: List[str]


def resolve_agent_type(
    explicit: Optional[str],
    tags: List[str],
    has_context: bool,
) -> AgentTypePolicy:
    if explicit:
        agent_type = explicit
    else:
        lowered = [t.lower() for t in tags]
        if "rag_grounding" in lowered or (has_context and "rag" in lowered):
            agent_type = "rag_agent"
        elif "tool" in lowered:
            agent_type = "tool_agent"
        elif "classification" in lowered:
            agent_type = "classification_agent"
        else:
            agent_type = "chat_agent"

    notes: List[str] = []
    if agent_type == "rag_agent":
        required = ["correctness", "instruction_following"]
        if has_context:
            required.append("grounding")
        else:
            notes.append("Sin retrieval_context; grounding no es obligatoria.")
        relevant = ["faithfulness", "contextual_precision", "hallucination", "answer_relevancy"]
    elif agent_type == "tool_agent":
        required = ["correctness", "instruction_following"]
        relevant = ["instruction_adherence", "task_success"]
    elif agent_type == "classification_agent":
        required = ["correctness"]
        relevant = ["task_success", "task_success_deterministic"]
    else:
        required = ["correctness", "instruction_following"]
        relevant = ["instruction_adherence", "answer_relevancy"]

    return AgentTypePolicy(
        agent_type=agent_type,
        required_dimensions=required,
        relevant_metrics=relevant,
        notes=notes,
    )
