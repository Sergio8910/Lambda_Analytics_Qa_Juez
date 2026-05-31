"""Fuentes de descarga de artefactos.

Cada fuente implementa `BaseSource` con un método `fetch(spec) -> bytes`.
Se enchufan por el campo `source.type` del webhook payload.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseSource, SourceError, SourceNotFoundError, SourceAuthError, SourceTimeoutError

# Registry. Se llena al importar cada implementación concreta.
_REGISTRY: Dict[str, Type[BaseSource]] = {}


def register_source(type_name: str, cls: Type[BaseSource]) -> None:
    _REGISTRY[type_name] = cls


def get_source(type_name: str) -> BaseSource:
    """Resuelve una fuente por su `type`. Lanza KeyError si no está registrada."""
    if type_name not in _REGISTRY:
        raise KeyError(f"Source type '{type_name}' no registrada. Disponibles: {sorted(_REGISTRY)}")
    return _REGISTRY[type_name]()


__all__ = [
    "BaseSource",
    "SourceError",
    "SourceNotFoundError",
    "SourceAuthError",
    "SourceTimeoutError",
    "register_source",
    "get_source",
]
