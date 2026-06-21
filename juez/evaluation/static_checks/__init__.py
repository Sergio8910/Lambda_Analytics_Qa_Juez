"""Chequeos estáticos generales aplicables a cualquier evaluador.

Aquí viven análisis que no dependen del motor LLM ni de ejecutar nada — solo
leen la configuración del agente (prompt, tools, schemas) y reportan
desalineaciones o inconsistencias.

Cada función toma datos normalizados y retorna problemas en el formato común:

    {"tipo": str, "descripcion": str, "nodo": str, "severidad": str}

donde `tipo` debe estar mapeado a una dimensión en el TIPO_A_DIMENSION
correspondiente del evaluador que invoca.
"""
from .alignment import check_tool_prompt_alignment
from .tool_security import check_tool_security

__all__ = ["check_tool_prompt_alignment", "check_tool_security"]
