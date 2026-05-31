"""Framework ADITIVO de QA de artefactos del Juez.

Permite disparar un flujo "por debajo" con datos sinteticos y evaluar el
artefacto que produce (PDF, filas DB, archivos). General y enchufable:
  - drivers/  -> como disparar (SyntheticDriver)
  - evaluators/ -> como evaluar la salida (OutputEvaluator)
  - specs/<agent_id>.json -> configuracion por agente

Punto de entrada: run.run_artifact_eval(agent_id, env).
"""
from .run import run_artifact_eval
from . import specs

__all__ = ["run_artifact_eval", "specs"]
