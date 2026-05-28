from __future__ import annotations

from evaluation.judge_engine import _is_success


def test_is_success_float_safe() -> None:
    assert _is_success(0.67, 0.67) is True
    assert _is_success(0.6666, 0.67) is False
