from __future__ import annotations

from evaluation.scorecard.agent_types import resolve_agent_type


def test_rag_agent_requires_grounding():
    policy = resolve_agent_type(None, ["rag_grounding"], True)
    assert policy.agent_type == "rag_agent"
    assert "grounding" in policy.required_dimensions
