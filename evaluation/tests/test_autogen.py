from __future__ import annotations

from evaluation.autogen.prompt_analyzer import analyze_prompt
from evaluation.autogen.case_generator import generate_cases
from evaluation.autogen.context_generator import generate_context_for_case


def test_autogen_distribution_and_context():
    profile = analyze_prompt("Responde en español y sin markdown.")
    cases = generate_cases(profile, n_cases=30, seed=7)
    assert len(cases) == 30
    tags = [t for c in cases for t in c.tags]
    assert "autogen" in tags
    # distribución: suma de tipos == n_cases
    type_counts = {t: 0 for t in ["happy_path", "edge", "adversarial", "stress"]}
    for c in cases:
        for t in c.tags:
            if t in type_counts:
                type_counts[t] += 1
    assert sum(type_counts.values()) == 30

    has_distractor = False
    for c in cases:
        ctx = generate_context_for_case(profile, c, seed=7)
        assert 3 <= len(ctx) <= 8
        for node in ctx:
            assert node.get("source") == "synthetic"
        if any(n.get("meta", {}).get("distractor") for n in ctx):
            has_distractor = True
    assert has_distractor


def test_autogen_determinism_seed():
    profile = analyze_prompt("Responde en español y sin markdown.")
    cases1 = generate_cases(profile, n_cases=10, seed=7)
    cases2 = generate_cases(profile, n_cases=10, seed=7)
    assert [c.case_id for c in cases1] == [c.case_id for c in cases2]
    assert [c.input for c in cases1] == [c.input for c in cases2]
    ctx1 = generate_context_for_case(profile, cases1[0], seed=7)
    ctx2 = generate_context_for_case(profile, cases2[0], seed=7)
    assert [c["text"] for c in ctx1] == [c["text"] for c in ctx2]
