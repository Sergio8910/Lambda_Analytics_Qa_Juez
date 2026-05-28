from __future__ import annotations

import hashlib

from evaluation.autogen.case_generator import build_cases
from evaluation.autogen.context_synth import synthesize_context


def _hash_cases(cases):
    joined = "|".join([c.case_id + c.input + ",".join(c.retrieval_context) for c in cases])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def test_autogen_seed_reproducible():
    nodes1 = synthesize_context(seed=42, n_nodes=6)
    cases1, _ = build_cases("prompt", nodes1, 20, seed=42)
    nodes2 = synthesize_context(seed=42, n_nodes=6)
    cases2, _ = build_cases("prompt", nodes2, 20, seed=42)
    assert _hash_cases(cases1) == _hash_cases(cases2)
