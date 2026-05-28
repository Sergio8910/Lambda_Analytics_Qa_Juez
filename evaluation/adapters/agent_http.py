from __future__ import annotations

from typing import Tuple

from ..report_models import EvaluationSpec


def call_agent_http(spec: EvaluationSpec, user_input: object) -> Tuple[str, list[str]]:
    if isinstance(user_input, dict) and "system_prompt" in user_input and "user_input" in user_input:
        raise NotImplementedError(
            "El adaptador HTTP no está implementado. Se esperaba enviar JSON con "
            "system_prompt, user_input y retrieval_context."
        )
    raise NotImplementedError(
        "El adaptador HTTP no está implementado. Se esperaba enviar JSON con user_input."
    )
