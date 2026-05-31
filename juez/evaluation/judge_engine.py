from __future__ import annotations

from .core.engine_impl import (
    JudgeEngine as _EngineImpl,
    extract_claims,
    score_claims_against_context,
    _translate_reason,
    _is_success,
)


class JudgeEngine(_EngineImpl):
    pass


__all__ = [
    "JudgeEngine",
    "extract_claims",
    "score_claims_against_context",
    "_translate_reason",
    "_is_success",
]
