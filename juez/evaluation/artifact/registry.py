"""Registro de drivers y evaluadores del framework de QA de artefactos.

Mantenerlo general y aditivo: agregar una capacidad = registrar una clase con el
decorador correspondiente. Agregar un agente = soltar un JSON en specs/.
No hay dispatch en el core; todo vive aqui.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Type

DRIVERS: Dict[str, Type] = {}
EVALUATORS: Dict[str, Type] = {}


def driver(name: str) -> Callable[[Type], Type]:
    def _wrap(cls: Type) -> Type:
        DRIVERS[name] = cls
        return cls
    return _wrap


def evaluator(name: str) -> Callable[[Type], Type]:
    def _wrap(cls: Type) -> Type:
        EVALUATORS[name] = cls
        return cls
    return _wrap


def make_driver(name: str, **cfg: Any):
    if name not in DRIVERS:
        raise KeyError(f"Driver '{name}' no registrado. Disponibles: {sorted(DRIVERS)}")
    return DRIVERS[name](**cfg)


def make_evaluator(name: str, **cfg: Any):
    if name not in EVALUATORS:
        raise KeyError(f"Evaluador '{name}' no registrado. Disponibles: {sorted(EVALUATORS)}")
    return EVALUATORS[name](**cfg)


def _cargar_builtins() -> None:
    """Importa los drivers/evaluadores incluidos para que se auto-registren."""
    from .drivers import n8n_webhook  # noqa: F401
    from .drivers import synthetic_pdf  # noqa: F401
    from .evaluators import pdf_db    # noqa: F401
    from .evaluators import synthetic_pdf as _synthetic_pdf_eval  # noqa: F401


_cargar_builtins()
