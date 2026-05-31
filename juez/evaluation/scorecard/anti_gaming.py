from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..report_models import TaskContract


@dataclass
class AntiGamingFlag:
    code: str
    severity: str
    reason: str


@dataclass
class AntiGamingResult:
    flags: List[AntiGamingFlag]
    penalty: Optional[Dict[str, Any]]
    notes: List[str]


_STOPWORDS = {
    "el",
    "la",
    "los",
    "las",
    "de",
    "y",
    "o",
    "a",
    "en",
    "que",
    "por",
    "para",
    "con",
}


def evaluate_anti_gaming(
    output: str, contract: TaskContract, config: Dict[str, Any] | None = None
) -> AntiGamingResult:
    cfg = config or {}
    flags: List[AntiGamingFlag] = []
    notes: List[str] = []
    penalty: Optional[Dict[str, Any]] = None

    lower = output.lower()
    repeated = lower.count("según el contexto") + lower.count("información disponible")
    if repeated >= 2:
        flags.append(
            AntiGamingFlag(
                code="repetitive_disclaimer",
                severity="low",
                reason="Se detectaron disclaimers repetitivos.",
            )
        )

    self_ref = any(p in lower for p in ["soy un evaluador", "como juez", "esta respuesta está en español"])
    if self_ref:
        flags.append(
            AntiGamingFlag(
                code="self_referential",
                severity="med",
                reason="Texto autoreferencial de evaluación.",
            )
        )

    if contract.output_format != "json" and output.strip().startswith("{"):
        flags.append(
            AntiGamingFlag(
                code="format_spoof",
                severity="low",
                reason="Salida con apariencia JSON sin ser requerida.",
            )
        )

    tokens = re.findall(r"[a-zA-Záéíóúñ]+", lower)
    if len(tokens) > 0:
        stop_ratio = sum(1 for t in tokens if t in _STOPWORDS) / max(len(tokens), 1)
        if stop_ratio > 0.7 and len(tokens) > 40:
            flags.append(
                AntiGamingFlag(
                    code="verbosity",
                    severity="low",
                    reason="Alta proporción de tokens no informativos.",
                )
            )

    # penalización simple
    if flags:
        penalty = {"dimension": "instruction_following", "delta": 0.05}
        notes.append("Se aplicó penalización por señales de gaming.")

    return AntiGamingResult(flags=flags, penalty=penalty, notes=notes)
