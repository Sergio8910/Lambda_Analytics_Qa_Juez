from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable, Dict, Tuple

from ..report_models import EvaluationSpec


def _load_callable(spec: EvaluationSpec) -> Callable[[str], Dict[str, Any]]:
    module = importlib.import_module(spec.agent_module)
    fn = getattr(module, spec.agent_function, None)
    if fn is None or not callable(fn):
        raise AttributeError(
            f"No se encontró la función '{spec.agent_function}' en el módulo '{spec.agent_module}'."
        )
    return fn


def call_agent(spec: EvaluationSpec, user_input: object) -> Tuple[str, list[str]]:
    fn = _load_callable(spec)
    payload: Dict[str, Any] | None = None
    if isinstance(user_input, dict) and "system_prompt" in user_input and "user_input" in user_input:
        payload = user_input

    if payload is not None:
        try:
            sig = inspect.signature(fn)
            params = sig.parameters
            if any(p.kind == p.VAR_KEYWORD for p in params.values()):
                result = fn(**payload)
            else:
                kwargs: Dict[str, Any] = {}
                for name in params:
                    if name in payload:
                        kwargs[name] = payload[name]
                    elif name in {"question", "query", "prompt", "user_input"}:
                        kwargs[name] = payload.get("user_input", "")
                    elif name in {"context", "contexts", "context_blocks", "retrieval_context"}:
                        kwargs[name] = payload.get("retrieval_context", [])
                    elif name in {"system_prompt", "system", "prompt_base"}:
                        kwargs[name] = payload.get("system_prompt", "")
                    elif name == "language":
                        kwargs[name] = payload.get("language", "es")
                    elif name == "no_markdown":
                        kwargs[name] = payload.get("no_markdown", True)
                    elif name == "output_format":
                        kwargs[name] = payload.get("output_format", "free_text")
                    elif name == "json_schema":
                        kwargs[name] = payload.get("json_schema")
                if kwargs:
                    result = fn(**kwargs)
                else:
                    result = fn(payload)
        except TypeError:
            result = fn(payload.get("user_input", ""))
    else:
        result = fn(str(user_input))
    if not isinstance(result, dict):
        raise TypeError("El agente debe retornar un dict con 'response' y 'retrieval_context'.")
    if "response" not in result:
        raise KeyError("El dict del agente no contiene la clave 'response'.")
    response = str(result.get("response", ""))
    retrieval_context = result.get("retrieval_context") or []
    if isinstance(retrieval_context, (str, bytes)):
        retrieval_context = [retrieval_context]
    if not isinstance(retrieval_context, list):
        retrieval_context = [str(retrieval_context)]
    retrieval_list = [str(x) for x in retrieval_context if x is not None]
    return response, retrieval_list
