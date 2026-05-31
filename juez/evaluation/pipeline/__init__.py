"""Pipeline multi-agente — análisis y evaluación de flujos completos."""

from .graph import (
    PipelineNode,
    PipelineEdge,
    PipelineGap,
    PipelineGraph,
    build_pipeline_graph,
)
from .analyzer import analizar_coherencia_pipeline
from .reporter import generar_reporte_pipeline

__all__ = [
    "PipelineNode",
    "PipelineEdge",
    "PipelineGap",
    "PipelineGraph",
    "build_pipeline_graph",
    "analizar_coherencia_pipeline",
    "generar_reporte_pipeline",
]
