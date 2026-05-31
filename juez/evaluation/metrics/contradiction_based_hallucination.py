from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..core.engine_impl import extract_claims, score_claims_against_context
from ..report_models import MetricResult


@dataclass
class ContradictionBasedHallucinationMetric:
    def evaluate(
        self,
        output: str,
        context: List[str],
        threshold: float,
        config: Dict[str, Any] | None = None,
    ) -> MetricResult:
        cfg = config or {}
        ignore_unverifiable = bool(cfg.get("ignore_unverifiable", True))
        claims = extract_claims(output)
        analysis = score_claims_against_context(
            claims,
            context,
            penalize_numbers=bool(cfg.get("penalize_numbers", False)),
        )
        supported = sum(1 for c in analysis.claims if c.verdict == "supported")
        contradicted = sum(1 for c in analysis.claims if c.verdict == "contradicted")
        unverifiable = sum(1 for c in analysis.claims if c.verdict == "unverifiable")

        if ignore_unverifiable:
            total_verifiable = supported + contradicted
        else:
            total_verifiable = supported + contradicted + unverifiable

        if total_verifiable <= 0:
            score = 1.0
        else:
            score = 1.0 - (contradicted / total_verifiable)

        reason = (
            f"Contradicciones={contradicted}, Verificables={total_verifiable}, "
            f"Unverificables={'ignorado' if ignore_unverifiable else unverifiable}."
        )
        return MetricResult(
            name="hallucination",
            score=score,
            threshold=threshold,
            success=None,
            reason=reason,
            reason_es=reason,
            raw={
                "supported": supported,
                "contradicted": contradicted,
                "unverifiable": unverifiable,
                "total_verifiable": total_verifiable,
                "ignore_unverifiable": ignore_unverifiable,
                "claim_analysis": analysis.model_dump(mode="json"),
            },
        )
