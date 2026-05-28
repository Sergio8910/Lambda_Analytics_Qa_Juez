from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.models import NormalizedRun
from ..report_models import EvaluationSpec, TestCase
from ..contracts import RunnerResult


class BaseAdapter(ABC):
    @abstractmethod
    def build_normalized_run(
        self, case: TestCase, raw_result: RunnerResult, spec: EvaluationSpec
    ) -> NormalizedRun:
        raise NotImplementedError
