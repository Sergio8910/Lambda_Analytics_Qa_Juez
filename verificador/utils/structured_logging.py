"""Logging estructurado opcional para verificador (text default | JSON via LOG_FORMAT=json).

Uso:
    from verificador.utils.structured_logging import setup_logging
    setup_logging(level="INFO")  # respeta env LOG_FORMAT

Redacta automáticamente valores asociados a claves sensibles
('password', 'token', 'key', 'secret', 'authorization', 'api_key') en los
campos del log del verificador.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict

_SENSITIVE_KEYS = ("password", "token", "key", "secret", "authorization", "api_key")
_SENSITIVE_PATTERN = re.compile(
    r"(" + "|".join(_SENSITIVE_KEYS) + r")(\s*[:=]\s*)"
    r"(?:(?:Bearer|Basic|Token)\s+)?"
    r"([^\s,;}\"]+)",
    re.IGNORECASE,
)


def _redact_text(msg: str) -> str:
    """Reemplaza valores tras 'token=xxx', 'password: xxx', etc. por ***REDACTED***.

    Maneja también esquemas tipo `Authorization: Bearer <token>` redactando
    el token completo después del scheme.
    """
    return _SENSITIVE_PATTERN.sub(r"\1\2***REDACTED***", msg)


class JsonFormatter(logging.Formatter):
    """Formatea cada record del verificador como una línea JSON con campos canónicos."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact_text(record.getMessage()),
            "file": record.filename,
            "line": record.lineno,
        }
        # Atributos extra que el usuario pase via logger.info(..., extra={...})
        # NO incluir attrs estándar de LogRecord — solo los custom.
        std_attrs = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "module", "msecs",
            "message", "msg", "name", "pathname", "process", "processName",
            "relativeCreated", "stack_info", "thread", "threadName", "taskName",
        }
        for attr, val in record.__dict__.items():
            if attr not in std_attrs and not attr.startswith("_"):
                payload[attr] = val
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class RedactingFilter(logging.Filter):
    """Redacta el mensaje plain-text antes del format (defensa para formatos no-JSON)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        return True


def setup_logging(level: str = "INFO", fmt: str | None = None) -> None:
    """Configura el root logger del verificador.

    fmt: "text" o "json". Si None: lee env `LOG_FORMAT` (default "text").
    """
    fmt = fmt or os.getenv("LOG_FORMAT", "text").lower()
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
        ))
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
