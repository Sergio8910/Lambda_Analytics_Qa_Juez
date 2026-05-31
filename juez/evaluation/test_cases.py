from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentTestCase:
    input: str
    expected_behavior: str
    context: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTestCase":
        input_text = str(data.get("input", "")).strip()
        expected = str(data.get("expected_behavior", "")).strip()
        context_raw = data.get("context", [])
        if context_raw is None:
            context_list: list[str] = []
        else:
            context_list = [str(item) for item in context_raw if item is not None]
        return cls(input=input_text, expected_behavior=expected, context=context_list)


TEST_CASES: list[dict[str, object]] = [
    {
        "input": (
            "Conversación:\n"
            "Usuario: ¿Qué productos de limpieza tienen y sus precios?\n"
        ),
        "expected_behavior": (
            "Responder con los productos de limpieza y sus precios exactos del inventario "
            "del Supermercado Euro, sin inventar datos."
        ),
        "context": [],
    }
]


GENERATED_CASES_PATH = Path(
    os.getenv("EVAL_GENERATED_CASES_PATH", Path(__file__).with_name("generated_cases.json"))
)


def _read_generated_cases(path: Path) -> List[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("Generated cases file is not a list: %s", path)
            return []
        cleaned: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                cleaned.append(item)
            else:
                logger.debug("Skipping non-dict case in generated file: %r", item)
        return cleaned
    except Exception as exc:
        logger.warning("Failed to read generated cases from %s: %s", path, exc)
        return []


def load_test_cases() -> list[AgentTestCase]:
    return [AgentTestCase.from_dict(item) for item in TEST_CASES]
