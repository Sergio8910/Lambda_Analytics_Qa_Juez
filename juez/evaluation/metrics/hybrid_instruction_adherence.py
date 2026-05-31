from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..feedback_generator import build_case_feedback, detect_language_es
from ..report_models import MetricResult, TaskContract


_MARKDOWN_PATTERNS = [
    re.compile(r"^\\s*#{1,6}\\s+.+", re.MULTILINE),
    re.compile(r"^\\s*[-*+]\\s+\\w+", re.MULTILINE),
    re.compile(r"```"),
    re.compile(r"\\*\\*.+?\\*\\*"),
    re.compile(r"\\[.+?\\]\\(.+?\\)"),
]


@dataclass
class HybridInstructionAdherenceMetric:
    expected_language: str = "es"
    no_markdown: bool = True

    def _language_score(self, output: str) -> float:
        if self.expected_language != "es":
            return 1.0
        detected = detect_language_es(output)
        return 1.0 if detected == "es" else 0.0

    def _format_score(self, output: str) -> float:
        if not self.no_markdown:
            return 1.0
        for pattern in _MARKDOWN_PATTERNS:
            if pattern.search(output):
                return 0.0
        return 1.0

    def _coverage_score(
        self,
        user_input: str,
        output: str,
        tags: list[str],
        context: list[str],
        llm_score: Optional[float] = None,
    ) -> float:
        feedback = build_case_feedback(
            user_input=user_input,
            output=output,
            tags=tags,
            metrics=[],
            claim_analysis=None,
            retrieval_context=context,
        )
        preguntas = feedback.question_by_question
        if preguntas:
            answered = sum(1 for q in preguntas if q.answered)
            return answered / max(len(preguntas), 1)
        # Fallback por overlap simple
        tokens_in = set(re.findall(r"[a-zA-ZÃ¡Ã©Ã­Ã³ÃºÃ±0-9]+", user_input.lower()))
        tokens_out = set(re.findall(r"[a-zA-ZÃ¡Ã©Ã­Ã³ÃºÃ±0-9]+", output.lower()))
        tokens_in = {t for t in tokens_in if len(t) > 3}
        if not tokens_in:
            base = 1.0
        else:
            base = 1.0 if tokens_in & tokens_out else 0.0
        if llm_score is None:
            return base
        return (base + llm_score) / 2.0

    def evaluate(
        self,
        user_input: str,
        output: str,
        tags: list[str],
        context: list[str],
        contract: TaskContract,
        threshold: float,
        llm_score: Optional[float] = None,
        llm_meta: Optional[Dict[str, Any]] = None,
    ) -> MetricResult:
        language_score = self._language_score(output)
        format_score = self._format_score(output)
        coverage_score = self._coverage_score(user_input, output, tags, context, llm_score)
        score = (0.4 * language_score) + (0.4 * coverage_score) + (0.2 * format_score)
        reason = (
            f"Idioma={language_score:.2f}, Cobertura={coverage_score:.2f}, "
            f"Formato={format_score:.2f}."
        )
        raw: Dict[str, Any] = {
            "language_score": language_score,
            "coverage_score": coverage_score,
            "format_score": format_score,
        }
        if llm_meta:
            raw["llm"] = llm_meta
        return MetricResult(
            name="instruction_adherence",
            score=score,
            threshold=threshold,
            success=None,
            reason=reason,
            reason_es=reason,
            raw=raw,
        )
