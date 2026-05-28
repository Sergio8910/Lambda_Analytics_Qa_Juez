from __future__ import annotations

from typing import Callable, Optional, Tuple, Any, Dict

from ..metric_registry import METRIC_RUNNERS
from ..report_models import MetricSpec


MetricRunner = Callable[[Any, Dict[str, Any], MetricSpec], Tuple[Any, Optional[Any]]]


class MetricRegistry:
    def __init__(self) -> None:
        self._runners = METRIC_RUNNERS

    def get(self, name: str) -> Optional[MetricRunner]:
        return self._runners.get(name)
