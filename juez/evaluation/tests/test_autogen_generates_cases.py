from __future__ import annotations

from juez.evaluation.autogen.case_generator import build_cases
from juez.evaluation.autogen.context_synth import synthesize_context


def test_autogen_generates_cases():
    nodes = synthesize_context(seed=7, n_nodes=6)
    cases, context_map = build_cases(
        prompt_base="Responde usando el contexto.",
        retrieval_nodes=nodes,
        n_cases=30,
        seed=7,
    )
    assert len(cases) == 30
    for c in cases:
        assert c.tags
        assert "synthetic" in c.tags
        assert c.retrieval_context
        assert c.case_id in context_map
