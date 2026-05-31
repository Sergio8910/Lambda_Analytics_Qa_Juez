"""Inspectores por tipo de artefacto.

Cada inspector implementa `BaseInspector.inspect(bytes, expected) -> InspectorReport`.
Se enchufa por el campo `artifact_type` del webhook payload.

Hoy: pdf. Mañana: image, video, audio, etc.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseInspector, InspectorError

_REGISTRY: Dict[str, Type[BaseInspector]] = {}


def register_inspector(artifact_type: str, cls: Type[BaseInspector]) -> None:
    _REGISTRY[artifact_type.lower()] = cls


def get_inspector(artifact_type: str) -> BaseInspector:
    """Resuelve un inspector por tipo de artefacto."""
    key = artifact_type.lower()
    if key not in _REGISTRY:
        raise KeyError(
            f"Artifact type '{artifact_type}' no tiene inspector registrado. "
            f"Disponibles: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]()


__all__ = [
    "BaseInspector",
    "InspectorError",
    "register_inspector",
    "get_inspector",
]
