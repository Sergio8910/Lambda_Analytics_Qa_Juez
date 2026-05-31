"""Protocolos del framework de QA de artefactos del Juez.

Define los contratos (duck typing, sin clases base) para:
  - SyntheticDriver: dispara un flujo/sub-flujo "por debajo" con datos sinteticos.
  - OutputEvaluator: evalua el artefacto producido (PDF, filas DB, archivos, etc.).

Es un paquete ADITIVO: no toca el core del Juez. Los resultados usan el mismo
modelo de severidad que `analisis["problemas"]` (CRITICO/ALTO/MEDIO/BAJO) para
fluir gratis por la logica de score/snapshot existente.
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

# Severidades — mismas que usa el analizador estatico de n8n.
SEVERIDADES = ("CRITICO", "ALTO", "MEDIO", "BAJO")


class Issue(TypedDict, total=False):
    tipo: str          # categoria legible, ej. "Artefacto / PDF"
    severidad: str     # CRITICO | ALTO | MEDIO | BAJO
    descripcion: str
    nodo: str          # opcional; "global" por defecto


class ArtifactContext(TypedDict, total=False):
    agent_id: str
    spec: Dict[str, Any]              # spec por-agente cargada de specs/<id>.json
    synthetic_input: Dict[str, Any]   # payload sintetico que se envio
    trigger_result: Dict[str, Any]    # salida cruda del SyntheticDriver
    env: Dict[str, str]               # variables de entorno relevantes


class ArtifactResult(TypedDict, total=False):
    nombre: str                 # nombre legible del evaluador
    score: float                # 0..100
    problemas: List[Issue]      # defectos REALES del artefacto (penalizan)
    reporte: str                # seccion de reporte lista para unir
    metricas: Dict[str, Any]    # datos crudos + notas de infraestructura QA


def mk_issue(severidad: str, descripcion: str, tipo: str = "Artefacto",
             nodo: str = "global") -> Issue:
    """Crea un Issue normalizado."""
    sev = severidad.upper()
    if sev not in SEVERIDADES:
        sev = "MEDIO"
    return {"tipo": tipo, "severidad": sev, "descripcion": descripcion, "nodo": nodo}


# Contratos (duck typing — documentacion, no se heredan):
#
# class SyntheticDriver:
#     def trigger(self, synthetic_input: Dict[str, Any]) -> Dict[str, Any]:
#         """Retorna {ok: bool, http_status: int|None, response: Any,
#                     latency_ms: float, error: str|None, raw: Any}."""
#
# class OutputEvaluator:
#     def evaluate(self, ctx: ArtifactContext) -> ArtifactResult: ...
