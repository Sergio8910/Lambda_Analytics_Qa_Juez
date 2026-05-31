"""Módulo contra-agente — evaluación mediante conversaciones multi-turno."""
from .models import (
    ConversationBatch,
    ConversationPlan,
    ConversationResult,
    BatchResult,
    TurnSpec,
    TurnResult,
    Persona,
    AdaptiveLogic,
)
from .generator import generar_batch
from .pool import ejecutar_batch
from .reporter import generar_reporte_batch

__all__ = [
    "ConversationBatch",
    "ConversationPlan",
    "ConversationResult",
    "BatchResult",
    "TurnSpec",
    "TurnResult",
    "Persona",
    "AdaptiveLogic",
    "generar_batch",
    "ejecutar_batch",
    "generar_reporte_batch",
]
