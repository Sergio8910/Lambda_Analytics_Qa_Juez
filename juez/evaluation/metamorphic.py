from __future__ import annotations

import random
import re
from typing import List

from .report_models import TestCase


def _seeded_rng(seed: int) -> random.Random:
    return random.Random(seed)


def generate_paraphrase(text: str, seed: int) -> str:
    rng = _seeded_rng(seed)
    replacements = [
        ("¿Puedes", "¿Podrías"),
        ("¿Tienen", "¿Cuentan con"),
        ("productos", "artículos"),
        ("precios", "costos"),
        ("promociones", "ofertas"),
        ("limpieza", "aseo"),
        ("higiene personal", "cuidado personal"),
        ("horarios", "horas de atención"),
    ]
    result = text
    rng.shuffle(replacements)
    for src, dst in replacements[:3]:
        result = re.sub(rf"\b{re.escape(src)}\b", dst, result, flags=re.IGNORECASE)
    return result


def inject_typos_noise(text: str, seed: int) -> str:
    rng = _seeded_rng(seed)
    words = text.split()
    if not words:
        return text
    idx = rng.randrange(len(words))
    w = words[idx]
    if len(w) > 3:
        pos = rng.randrange(1, len(w) - 1)
        w = w[:pos] + w[pos + 1] + w[pos] + w[pos + 2 :]
        words[idx] = w
    if rng.random() < 0.5:
        words.insert(rng.randrange(len(words)), "")
    return " ".join(words).replace("  ", " ").strip()


def reorder_or_split(text: str, seed: int) -> str:
    rng = _seeded_rng(seed)
    parts = re.split(r"(?<=[\.\?\!])\s+", text.strip())
    parts = [p for p in parts if p]
    if len(parts) >= 2 and rng.random() < 0.7:
        rng.shuffle(parts)
        return " ".join(parts)
    if len(text) > 60:
        mid = len(text) // 2
        return text[:mid].strip() + ". " + text[mid:].strip()
    return text


def build_variants(testcase: TestCase, k: int, seed: int) -> List[TestCase]:
    variants: List[TestCase] = []
    base_text = testcase.input
    funcs = [generate_paraphrase, inject_typos_noise, reorder_or_split]
    for i in range(k):
        func = funcs[i % len(funcs)]
        new_input = func(base_text, seed + i + 1)
        new_case = TestCase(
            case_id=f"{testcase.case_id}-v{i+1}",
            input=new_input,
            tags=list(set(testcase.tags + ["metamorphic"])),
            severity=testcase.severity,
            task_contract=testcase.task_contract,
            expected_behavior=testcase.expected_behavior,
            context=list(testcase.context),
            turns=list(testcase.turns) if testcase.turns else None,
        )
        variants.append(new_case)
    return variants
