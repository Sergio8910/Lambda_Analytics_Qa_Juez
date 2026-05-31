from __future__ import annotations

from juez.evaluation.scorecard.scorecard import compute_scorecard
from juez.evaluation.scorecard.dimensions import DimensionResult, DimensionEvidence


def test_scorecard_weighting():
    dims = {
        "correctness": DimensionResult(0.8, [DimensionEvidence("task_success", 0.8, True)], []),
        "instruction_following": DimensionResult(0.6, [DimensionEvidence("instruction_adherence", 0.6, True)], []),
    }
    weights = {"correctness": 2.0, "instruction_following": 1.0}
    scorecard = compute_scorecard(dims, weights, {"min_overall_score": 0.7})
    assert scorecard.overall_score is not None
    assert scorecard.scorecard_passed is True
