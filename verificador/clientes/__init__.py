"""Adapters por cliente.

Cada cliente sabe (a) conectarse a su BD productiva en read-only y (b)
construir un `ExpectedSnapshot` dado un `artifact_id`. El verifier es
agnóstico al cliente — solo lo resuelve por nombre.
"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseClientAdapter, ClientError, ClientNotFoundError, ClientDBError

_REGISTRY: Dict[str, Type[BaseClientAdapter]] = {}


def register_client(name: str, cls: Type[BaseClientAdapter]) -> None:
    _REGISTRY[name.lower()] = cls


def get_client(name: str) -> BaseClientAdapter:
    """Resuelve un cliente por nombre. Lanza KeyError si no está registrado."""
    key = name.lower()
    if key not in _REGISTRY:
        raise KeyError(f"Cliente '{name}' no registrado. Disponibles: {sorted(_REGISTRY)}")
    return _REGISTRY[key]()


__all__ = [
    "BaseClientAdapter",
    "ClientError",
    "ClientNotFoundError",
    "ClientDBError",
    "register_client",
    "get_client",
]
