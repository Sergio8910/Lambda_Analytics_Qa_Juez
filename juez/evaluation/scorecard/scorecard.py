from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .dimensions import DimensionResult


@dataclass
class ScorecardResult:
    overall_score: Optional[float]
    weights: Dict[str, float]
    eligible_dimensions: List[str]
    scorecard_passed: Optional[bool]
    gates: Dict[str, Any]
    notes: List[str]


def compute_scorecard(
    dimensions: Dict[str, DimensionResult],
    weights: Dict[str, float],
    gates: Dict[str, Any],
) -> ScorecardResult:
    notes: List[str] = []
    eligible = [k for k, v in dimensions.items() if v.score is not None]
    if not weights:
        weights = {k: 1.0 for k in eligible}
    total_w = 0.0
    total = 0.0
    for dim in eligible:
        w = float(weights.get(dim, 1.0))
        total_w += w
        total += (dimensions[dim].score or 0.0) * w
    overall = (total / total_w) if total_w > 0 else None

    scorecard_passed = None
    min_overall = gates.get("min_overall_score")
    must_pass = gates.get("must_pass_dimensions", {})
    if overall is not None or must_pass:
        scorecard_passed = True
        if min_overall is not None and overall is not None and overall < float(min_overall):
            scorecard_passed = False
            notes.append("overall_score por debajo del mínimo.")
        for dim, min_score in must_pass.items():
            if dim in dimensions and dimensions[dim].score is not None:
                if dimensions[dim].score < float(min_score):
                    scorecard_passed = False
                    notes.append(f"Dimensión {dim} por debajo del mínimo.")
            else:
                scorecard_passed = False
                notes.append(f"Dimensión {dim} sin score.")

    return ScorecardResult(
        overall_score=overall,
        weights=weights,
        eligible_dimensions=eligible,
        scorecard_passed=scorecard_passed,
        gates=gates or {},
        notes=notes,
    )
