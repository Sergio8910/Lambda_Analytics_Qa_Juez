"""Contextos aislados y presupuestos para las abejas de La Colmena.

El contexto conserva metadatos y resúmenes, nunca prompts ni respuestas
completas. De esa forma la Reina puede consolidar el trabajo sin duplicar todo
el contexto de cada obrera ni persistir secretos del proyecto.
"""
from __future__ import annotations

import math
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


class BeeBudgetExceeded(RuntimeError):
    """La abeja o el enjambre agotó su presupuesto antes de una llamada LLM."""


def estimate_tokens(value: Any) -> int:
    """Estimación conservadora cuando el proveedor no entrega ``usage``.

    La aproximación de cuatro caracteres por token no pretende facturar; sirve
    para límites operativos y se marca siempre como estimada.
    """
    if value is None:
        return 0
    text = value if isinstance(value, str) else str(value)
    return max(1, math.ceil(len(text) / 4)) if text else 0


@dataclass
class BeeContext:
    evaluation_id: str
    bee_id: str
    role: str
    component: str
    context_limit_tokens: int
    budget_tokens: int
    _reserve_global: Callable[[int], None] = field(repr=False)
    _track_global_completion: Callable[[int], None] = field(repr=False)
    conversation_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    context_tokens: int = 0
    context_rotations: int = 0
    findings_count: int = 0
    severities: dict[str, int] = field(default_factory=dict)
    result_summaries: list[str] = field(default_factory=list)
    status: str = "ready"
    last_error: str = ""
    usage_estimated: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def prepare_prompt(self, prompt: str) -> int:
        tokens = estimate_tokens(prompt)
        with self._lock:
            if self.total_tokens + tokens > self.budget_tokens:
                self.status = "budget_exhausted"
                raise BeeBudgetExceeded(
                    f"La abeja {self.bee_id} agotó su presupuesto de "
                    f"{self.budget_tokens} tokens estimados"
                )
            self._reserve_global(tokens)
            if self.context_tokens + tokens > self.context_limit_tokens:
                self.conversation_id = None
                self.context_tokens = 0
                self.context_rotations += 1
            self.prompt_tokens += tokens
            self.context_tokens += tokens
            self.calls += 1
            self.status = "running"
        return tokens

    def record_completion(self, reply: str) -> int:
        tokens = estimate_tokens(reply)
        self._track_global_completion(tokens)
        with self._lock:
            self.completion_tokens += tokens
            self.context_tokens += tokens
            if self.total_tokens >= self.budget_tokens:
                self.status = "budget_exhausted"
        return tokens

    def set_conversation_id(self, conversation_id: str) -> None:
        with self._lock:
            self.conversation_id = conversation_id

    def record_findings(self, findings: list[Any]) -> None:
        with self._lock:
            for finding in findings:
                if hasattr(finding, "model_dump"):
                    data = finding.model_dump()
                elif isinstance(finding, dict):
                    data = finding
                else:
                    data = {"title": str(finding)}
                severity = str(data.get("severity") or data.get("severidad") or "info")
                self.severities[severity] = self.severities.get(severity, 0) + 1
                summary = (
                    data.get("title")
                    or data.get("descripcion")
                    or data.get("description")
                    or ""
                )
                if summary and len(self.result_summaries) < 8:
                    self.result_summaries.append(str(summary)[:240])
                self.findings_count += 1
            if self.status not in {"budget_exhausted", "failed"}:
                self.status = "completed"

    def fail(self, exc: Exception) -> None:
        with self._lock:
            self.status = "failed"
            self.last_error = f"{type(exc).__name__}: {str(exc)[:240]}"

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "bee_id": self.bee_id,
                "role": self.role,
                "component": self.component,
                "status": self.status,
                "isolated_conversation": True,
                "has_conversation_id": bool(self.conversation_id),
                "calls": self.calls,
                "prompt_tokens_estimated": self.prompt_tokens,
                "completion_tokens_estimated": self.completion_tokens,
                "total_tokens_estimated": self.total_tokens,
                "context_tokens_estimated": self.context_tokens,
                "context_limit_tokens": self.context_limit_tokens,
                "budget_tokens": self.budget_tokens,
                "context_rotations": self.context_rotations,
                "findings_count": self.findings_count,
                "severities": dict(self.severities),
                "result_summaries": list(self.result_summaries),
                "usage_estimated": self.usage_estimated,
                "last_error": self.last_error or None,
            }


class SwarmContextRegistry:
    """Registro thread-safe compartido por la Reina durante una evaluación."""

    def __init__(
        self,
        evaluation_id: str,
        *,
        context_limit_tokens: int | None = None,
        per_bee_budget_tokens: int | None = None,
        global_budget_tokens: int | None = None,
    ) -> None:
        self.evaluation_id = evaluation_id
        self.context_limit_tokens = context_limit_tokens or int(
            os.getenv("JUEZ_BEE_CONTEXT_LIMIT_TOKENS", "1000000")
        )
        self.per_bee_budget_tokens = per_bee_budget_tokens or int(
            os.getenv("JUEZ_BEE_BUDGET_TOKENS", "1000000")
        )
        self.global_budget_tokens = global_budget_tokens or int(
            os.getenv("JUEZ_SWARM_BUDGET_TOKENS", "8000000")
        )
        self._contexts: dict[str, BeeContext] = {}
        self._global_prompt_tokens = 0
        self._global_completion_tokens = 0
        self._lock = threading.Lock()

    def _reserve_global(self, tokens: int) -> None:
        with self._lock:
            current = self._global_prompt_tokens + self._global_completion_tokens
            if current + tokens > self.global_budget_tokens:
                raise BeeBudgetExceeded(
                    f"El enjambre agotó su presupuesto global de "
                    f"{self.global_budget_tokens} tokens estimados"
                )
            self._global_prompt_tokens += tokens

    def _track_global_completion(self, tokens: int) -> None:
        with self._lock:
            self._global_completion_tokens += tokens

    def get_or_create(self, bee_id: str, role: str, component: str) -> BeeContext:
        key = f"{bee_id}::{component}"
        with self._lock:
            context = self._contexts.get(key)
            if context is None:
                context = BeeContext(
                    evaluation_id=self.evaluation_id,
                    bee_id=bee_id,
                    role=role,
                    component=component,
                    context_limit_tokens=self.context_limit_tokens,
                    budget_tokens=self.per_bee_budget_tokens,
                    _reserve_global=self._reserve_global,
                    _track_global_completion=self._track_global_completion,
                )
                self._contexts[key] = context
            return context

    def report(self) -> dict[str, Any]:
        with self._lock:
            contexts = list(self._contexts.values())
            prompt_tokens = self._global_prompt_tokens
            completion_tokens = self._global_completion_tokens
        bees = [context.report() for context in contexts]
        bees.sort(key=lambda item: (item["role"], item["component"], item["bee_id"]))
        return {
            "evaluation_id": self.evaluation_id,
            "architecture": "isolated_context_per_bee",
            "usage_source": "provider_usage_or_local_estimate",
            "context_limit_tokens_per_bee": self.context_limit_tokens,
            "budget_tokens_per_bee": self.per_bee_budget_tokens,
            "global_budget_tokens": self.global_budget_tokens,
            "bee_count": len(bees),
            "total_calls": sum(item["calls"] for item in bees),
            "total_prompt_tokens_estimated": prompt_tokens,
            "total_completion_tokens_estimated": completion_tokens,
            "total_tokens_estimated": prompt_tokens + completion_tokens,
            "queen_digest": bees,
        }


_CURRENT_BEE: ContextVar[BeeContext | None] = ContextVar(
    "juez_current_bee_context", default=None
)


def current_bee_context() -> BeeContext | None:
    return _CURRENT_BEE.get()


@contextmanager
def activate_bee(
    registry: SwarmContextRegistry,
    *,
    bee_id: str,
    role: str,
    component: str,
) -> Iterator[BeeContext]:
    context = registry.get_or_create(bee_id, role, component)
    token = _CURRENT_BEE.set(context)
    try:
        yield context
    except Exception as exc:
        context.fail(exc)
        raise
    finally:
        _CURRENT_BEE.reset(token)
