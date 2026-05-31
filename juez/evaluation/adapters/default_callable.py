from __future__ import annotations

import inspect

from ..core.contracts import resolve_contract, to_contract_info
from ..core.models import (
    AgentInfo,
    CaseInfo,
    ContextInfo,
    ExecutionInfo,
    ExecutionTrace,
    InputInfo,
    NormalizedRun,
)
from ..report_models import EvaluationSpec, TestCase
from ..contracts import RunnerResult, AgentEnvelope
from ..normalize import normalize_agent_result
from .base import BaseAdapter


class DefaultCallableAdapter(BaseAdapter):
    def invoke(self, fn, spec: EvaluationSpec, case: TestCase):
        kwargs = {
            "user_input": case.input,
            "input": case.input,
            "prompt": case.input,
            "query": case.input,
            "message": case.input,
            "tags": case.tags,
            "case_id": case.case_id,
            "severity": case.severity,
            "expected_behavior": case.expected_behavior,
        }
        try:
            sig = inspect.signature(fn)
            params = sig.parameters
        except (TypeError, ValueError):
            return fn(case.input)

        if any(p.kind == p.VAR_KEYWORD for p in params.values()):
            try:
                return fn(**kwargs)
            except TypeError:
                pass

        filtered = {k: v for k, v in kwargs.items() if k in params}
        if filtered:
            try:
                return fn(**filtered)
            except TypeError:
                pass

        for args in ((spec, case), (case, spec), (case,), (case.input,)):
            try:
                return fn(*args)
            except TypeError:
                continue

        return fn(case.input)

    def invoke_normalized(
        self, fn, spec: EvaluationSpec, case: TestCase
    ) -> AgentEnvelope:
        raw = self.invoke(fn, spec, case)
        return normalize_agent_result(raw)

    def build_normalized_run(
        self, case: TestCase, raw_result: RunnerResult | dict, spec: EvaluationSpec
    ) -> NormalizedRun:
        contract = resolve_contract(spec, case)
        system_prompt = spec.prompt_base or ""
        conversation = []
        if case.turns:
            for t in case.turns:
                conversation.append({"role": "user", "content": t})
        else:
            conversation.append({"role": "user", "content": case.input})
        allowed_kinds = {
            "chat",
            "rag_chat",
            "tool_agent",
            "structured_generator",
            "classifier",
            "extractor",
            "voice_agent",
        }
        kind = spec.agent_kind if spec.agent_kind in allowed_kinds else "chat"
        output_text = ""
        retrieval_context = []
        latency_ms = 0.0
        if isinstance(raw_result, dict):
            output_text = (
                raw_result.get("output_text")
                or raw_result.get("response")
                or raw_result.get("text")
                or raw_result.get("content")
                or raw_result.get("answer")
                or ""
            )
            retrieval_context = raw_result.get("retrieval_context") or raw_result.get("context") or []
            has_key = any(
                k in raw_result for k in ("output_text", "response", "text", "content", "answer")
            )
            if output_text == "" and has_key:
                assert False, "output_text vacío pese a existir una clave válida."
        else:
            output_text = raw_result.output_text
            retrieval_context = raw_result.retrieval_context
            latency_ms = raw_result.latency_ms
        return NormalizedRun(
            run_id=spec.run_id,
            agent=AgentInfo(
                name=spec.agent_module,
                version="unknown",
                kind=kind,
            ),
            case=CaseInfo(
                case_id=case.case_id,
                tags=case.tags,
                severity=case.severity,
            ),
            input=InputInfo(
                user_message=case.input,
                system_prompt=system_prompt,
                conversation=conversation,
            ),
            context=ContextInfo(
                provided_context=case.context,
                retrieval_context=retrieval_context or case.retrieval_context,
                tools_available=[],
            ),
            execution=ExecutionInfo(
                output_text=output_text,
                output_json=None,
                tool_calls=[],
                tool_results=[],
                trace=ExecutionTrace(latency_ms=latency_ms),
            ),
            contract=to_contract_info(contract),
        )
