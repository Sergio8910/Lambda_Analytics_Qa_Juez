"""Audit trail para La Colmena + Auto-Fix."""
from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    timestamp: str
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class AuditLog(BaseModel):
    project_id: str
    started_at: str = Field(default_factory=lambda: _now())
    events: list[AuditEvent] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def add(self, event: str, **detail: Any) -> None:
        self.events.append(AuditEvent(timestamp=_now(), event=event, detail=detail))

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def unified_diff(before: str, after: str, *, fromfile: str, tofile: str | None = None) -> str:
    """Devuelve un diff unificado compacto para el reporte de auditoria."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile or fromfile,
            lineterm="",
        )
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
