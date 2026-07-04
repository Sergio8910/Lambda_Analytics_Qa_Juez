"""Carga de specs por-agente del framework de QA de artefactos."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_SPECS_DIR = Path(__file__).parent / "specs"


def spec_path(agent_id: str) -> Path:
    return _SPECS_DIR / f"{agent_id}.json"


def load_spec(agent_id: str) -> Optional[Dict[str, Any]]:
    """Carga la spec del agente. Retorna None si no existe."""
    p = spec_path(agent_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
