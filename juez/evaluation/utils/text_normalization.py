from __future__ import annotations

import re
from typing import Any


_MOJIBAKE_PAT = re.compile(r"[ÃÂâ]")


def repair_text(text: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if _MOJIBAKE_PAT.search(text):
        try:
            fixed = text.encode("latin1").decode("utf-8")
            if fixed:
                return fixed
        except Exception:
            pass
    try:
        import ftfy  # type: ignore

        return ftfy.fix_text(text)
    except Exception:
        return text


def repair_recursive(obj: Any) -> Any:
    if isinstance(obj, str):
        return repair_text(obj)
    if isinstance(obj, list):
        return [repair_recursive(x) for x in obj]
    if isinstance(obj, dict):
        return {k: repair_recursive(v) for k, v in obj.items()}
    return obj

