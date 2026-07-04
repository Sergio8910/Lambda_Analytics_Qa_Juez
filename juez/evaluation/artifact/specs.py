"""Carga de specs por-agente del framework de QA de artefactos."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_SPECS_DIR = Path(__file__).parent / "specs"


def spec_path(agent_id: str) -> Path:
    return _SPECS_DIR / f"{agent_id}.json"


def existe_spec(agent_id: str) -> bool:
    return spec_path(agent_id).exists()


def load_spec(agent_id: str) -> Optional[Dict[str, Any]]:
    """Carga la spec del agente. Retorna None si no existe."""
    p = spec_path(agent_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def listar_specs() -> List[str]:
    if not _SPECS_DIR.exists():
        return []
    return sorted(p.stem for p in _SPECS_DIR.glob("*.json"))


def resolver_agent_id(nombre_flujo: str) -> str:
    """Normaliza el nombre de un flujo a un agent_id (igual que evaluar_n8n)."""
    return nombre_flujo.lower().replace(" ", "_")[:40]
