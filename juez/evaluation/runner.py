from __future__ import annotations

import time
import importlib
import inspect
from typing import Optional, Tuple, Any

import multiprocessing as mp
import threading

from .adapters.agent_http import call_agent_http
from .adapters.default_callable import DefaultCallableAdapter
from .contracts import RunnerResult
from .report_models import AgentEvalInput, EvaluationSpec, TestCase


def _invoke_agent_raw(spec: EvaluationSpec, user_input: object) -> Any:
    module = importlib.import_module(spec.agent_module)
    fn = getattr(module, spec.agent_function)
    payload: dict[str, Any] | None = None
    if isinstance(user_input, dict) and "system_prompt" in user_input and "user_input" in user_input:
        payload = user_input
    if payload is not None:
        try:
            sig = inspect.signature(fn)
            params = sig.parameters
            if any(p.kind == p.VAR_KEYWORD for p in params.values()):
                return fn(**payload)
            kwargs: dict[str, Any] = {}
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
                return fn(**kwargs)
            return fn(payload)
        except TypeError:
            return fn(payload.get("user_input", ""))
    return fn(str(user_input))


def _process_worker(spec_data: dict, input_data: object, queue: mp.Queue) -> None:
    try:
        spec = EvaluationSpec(**spec_data)
        if spec.agent_kind == "http":
            output, ctx = call_agent_http(spec, input_data)
            raw_result = {"response": output, "retrieval_context": ctx}
        else:
            raw_result = _invoke_agent_raw(spec, input_data)
        queue.put({"raw_result": raw_result})
    except Exception as exc:
        queue.put({"error": str(exc)})


def _run_with_thread(
    spec: EvaluationSpec, input_data: object
) -> Tuple[Optional[object], Optional[str]]:
    result_container: dict[str, object] = {}

    def _target() -> None:
        try:
            if spec.agent_kind == "http":
                output, ctx = call_agent_http(spec, input_data)
                result_container["raw_result"] = {"response": output, "retrieval_context": ctx}
            else:
                result_container["raw_result"] = _invoke_agent_raw(spec, input_data)
        except Exception as exc:
            result_container["error"] = str(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=spec.agent_timeout_s)
    if thread.is_alive():
        return None, "Timeout (hilo)."
    if "error" in result_container:
        return None, str(result_container["error"])
    return result_container.get("raw_result"), None


def _run_with_timeout(
    spec: EvaluationSpec, input_data: object
) -> Tuple[Optional[object], Optional[str]]:
    try:
        ctx = mp.get_context("spawn")
        queue: mp.Queue = ctx.Queue()
    except Exception:
        return _run_with_thread(spec, input_data)
    proc = ctx.Process(
        target=_process_worker, args=(spec.model_dump(), input_data, queue), daemon=True
    )
    proc.start()
    proc.join(timeout=spec.agent_timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=1.0)
        return None, "Timeout (proceso terminado)."
    if queue.empty():
        return None, "El proceso terminó sin respuesta."
    result = queue.get()
    if "error" in result:
        return None, str(result.get("error"))
    return result.get("raw_result"), None


def _build_agent_eval_input(spec: EvaluationSpec, user_input: str, retrieval_context: list[str]) -> AgentEvalInput:
    system_prompt = getattr(spec, "prompt_base", None) or ""
    return AgentEvalInput(
        system_prompt=system_prompt,
        user_input=user_input,
        retrieval_context=retrieval_context,
        language=spec.instruction_policy.language,
        no_markdown=spec.instruction_policy.no_markdown,
        output_format=spec.task_contract_default.output_format,
        json_schema=spec.task_contract_default.json_schema,
    )


def _normalize_input(spec: EvaluationSpec, input_data: object) -> object:
    if isinstance(input_data, AgentEvalInput):
        return input_data.model_dump()
    if isinstance(input_data, TestCase):
        retrieval_ctx = input_data.retrieval_context or input_data.context or []
        needs_envelope = bool(spec.prompt_base) or bool(retrieval_ctx)
        if needs_envelope:
            agent_input = _build_agent_eval_input(spec, input_data.input, retrieval_ctx)
            return agent_input.model_dump()
        return input_data.input
    if isinstance(input_data, dict):
        if "system_prompt" in input_data and "user_input" in input_data:
            return input_data
    return input_data


def run_agent(spec: EvaluationSpec, input_data: object) -> RunnerResult:
    if isinstance(input_data, TestCase):
        test_case = input_data
        user_input = input_data.input
    else:
        user_input = str(input_data)
        test_case = TestCase(
            case_id="adhoc",
            input=user_input,
            tags=[],
            severity="baja",
            expected_behavior="",
        )

    module = importlib.import_module(spec.agent_module)
    fn = getattr(module, spec.agent_function)
    adapter = DefaultCallableAdapter()

    start = time.perf_counter()
    error: Optional[str] = None
    raw_result: object = {}
    if spec.agent_kind == "http":
        try:
            output, ctx = call_agent_http(spec, user_input)
            raw_result = {"response": output, "retrieval_context": ctx}
        except Exception as exc:
            error = str(exc)
            raw_result = {}
        def _http_fn(*_args, **_kwargs):
            return raw_result
        envelope = adapter.invoke_normalized(_http_fn, spec, test_case)
    else:
        raw_result = adapter.invoke(fn, spec, test_case)
        envelope = adapter.invoke_normalized(lambda *_args, **_kwargs: raw_result, spec, test_case)

    if not isinstance(raw_result, (dict, RunnerResult)):
        error = error or "Formato de respuesta invalido."

    latency_ms = (time.perf_counter() - start) * 1000.0
    if envelope.latency_ms is None:
        envelope.latency_ms = latency_ms

    return RunnerResult(
        output_text=envelope.output_text or "",
        retrieval_context=envelope.retrieval_context or [],
        latency_ms=envelope.latency_ms or latency_ms,
        error=error,
        envelope=envelope,
    )
