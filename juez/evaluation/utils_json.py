from __future__ import annotations

import json
import re
from typing import Any

from .report_models import RunReport
from .utils.text_normalization import repair_recursive


_SECRET_REGEX = re.compile(r"sk-[A-Za-z0-9_\-]{10,}")
_SECRET_KEYS = {
    "OPENAI_API_KEY",
    "LANGWATCH_API_KEY",
    "DEEPEVAL_API_KEY",
    "API_KEY",
}


def redact_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if k in _SECRET_KEYS:
                redacted[k] = "sk-***REDACTED***"
            else:
                redacted[k] = redact_secrets(v)
        return redacted
    if isinstance(obj, list):
        return [redact_secrets(x) for x in obj]
    if isinstance(obj, str):
        return _SECRET_REGEX.sub("sk-***REDACTED***", obj)
    return obj


def dump_json(obj: Any, indent: int = 2, redact: bool = True) -> str:
    if isinstance(obj, RunReport):
        data = obj.model_dump(mode="json")
        if hasattr(obj.summary, "to_dict"):
            data["summary"] = obj.summary.to_dict()
    elif hasattr(obj, "model_dump"):
        data = obj.model_dump(mode="json")
    else:
        data = obj
    if redact:
        data = redact_secrets(data)
    data = repair_recursive(data)
    return json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False)


def render_case_json(report: RunReport, case_id: str, indent: int = 2, redact: bool = True) -> str:
    case = next((c for c in report.cases if c.case_id == case_id), None)
    if not case:
        raise KeyError(f"No se encontró el caso {case_id}")
    return dump_json(case, indent=indent, redact=redact)


def render_run_json(report: RunReport, indent: int = 2, redact: bool = True) -> str:
    return dump_json(report, indent=indent, redact=redact)
