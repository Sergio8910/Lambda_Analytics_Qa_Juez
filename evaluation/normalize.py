from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Dict, List, Optional

from .contracts import AgentEnvelope, ToolCall, Usage


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, tuple) or isinstance(value, set):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value]
    return [str(value)]


def _extract_text_from_message(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "message", "response"):
            if key in value and value[key] is not None:
                return str(value[key])
    return str(value)


def _extract_tool_calls(value: Any) -> List[ToolCall]:
    if not value:
        return []
    calls: List[ToolCall] = []
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, ToolCall):
                calls.append(item)
                continue
            if isinstance(item, dict):
                name = (
                    item.get("name")
                    or item.get("tool")
                    or item.get("function")
                    or item.get("call")
                    or "tool"
                )
                args = item.get("arguments") or item.get("args")
                call_id = item.get("id") or item.get("call_id")
                calls.append(ToolCall(name=str(name), arguments=args, call_id=call_id))
            else:
                calls.append(ToolCall(name=str(item)))
    else:
        calls.append(ToolCall(name=str(value)))
    return calls


def normalize_agent_result(raw: Any) -> AgentEnvelope:
    if isinstance(raw, AgentEnvelope):
        if raw.output_text is None:
            raw.output_text = ""
        if raw.retrieval_context is None:
            raw.retrieval_context = []
        return raw

    output_text = ""
    retrieval_context: List[str] = []
    output_json: Optional[Dict[str, Any]] = None
    labels: Optional[Dict[str, Any]] = None
    tool_calls: List[ToolCall] = []
    usage: Optional[Usage] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    latency_ms: Optional[float] = None
    warnings: List[str] = []
    errors: List[str] = []

    raw_obj: Any = raw
    if raw is None:
        raw_obj = ""
    elif not isinstance(raw, (str, dict)) and hasattr(raw, "__dict__"):
        raw_obj = vars(raw)

    if isinstance(raw_obj, dict):
        for key in ("output_text", "response", "text", "answer", "output", "message"):
            if key in raw_obj and raw_obj[key] is not None:
                output_text = _extract_text_from_message(raw_obj[key])
                break
        retrieval_context = (
            raw_obj.get("retrieval_context")
            or raw_obj.get("context")
            or raw_obj.get("docs")
            or raw_obj.get("sources")
            or []
        )
        output_json = raw_obj.get("json") or raw_obj.get("payload") or raw_obj.get("data")
        labels = raw_obj.get("labels")
        tool_calls = _extract_tool_calls(
            raw_obj.get("tool_calls") or raw_obj.get("tools") or raw_obj.get("function_calls")
        )
        model = raw_obj.get("model")
        finish_reason = raw_obj.get("finish_reason")
        latency_ms = raw_obj.get("latency_ms")
        usage_raw = raw_obj.get("usage")
        if isinstance(usage_raw, dict):
            usage = Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
                completion_tokens=int(usage_raw.get("completion_tokens", 0)),
                total_tokens=int(usage_raw.get("total_tokens", 0)),
            )
    elif isinstance(raw_obj, str):
        output_text = raw_obj
    elif isinstance(raw_obj, Iterable):
        parts = []
        for item in raw_obj:
            if item is None:
                continue
            parts.append(str(item))
        output_text = "".join(parts)
    else:
        output_text = str(raw_obj)

    retrieval_context = _as_list(retrieval_context)

    if not output_text and output_json is not None:
        output_text = str(output_json)

    if output_text is None:
        output_text = ""

    return AgentEnvelope(
        output_text=output_text,
        retrieval_context=retrieval_context,
        output_json=output_json,
        labels=labels,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        latency_ms=latency_ms,
        model=model,
        usage=usage,
        raw=raw,
        warnings=warnings,
        errors=errors,
    )
