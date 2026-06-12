from __future__ import annotations

import base64
import os
import json
from io import BytesIO
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import ValidationError

from juez.evaluation.autogen.prompt_analyzer import analyze_prompt
from juez.evaluation.autogen.context_synth import synthesize_context
from juez.evaluation.autogen.case_generator import build_cases as build_autogen_cases
from juez.evaluation.autogen.case_generator import generate_cases as generate_autogen_cases
from juez.evaluation.autogen.agent_client import AgentHttpClient
from juez.evaluation.autogen import run_auto_eval
from juez.evaluation.autogen.schemas import AutoGenRequest
from juez.evaluation.core.engine import EvaluationEngine
from juez.evaluation.report_models import EvaluationSpec, TestCase, MetricResult, MetricSpec
from juez.evaluation.reporting.forensic import build_forensic_report, render_forensic_pdf
from juez.evaluation.runner import run_agent
from juez.evaluation.contracts import RunnerResult
from juez.evaluation.utils.text_normalization import repair_recursive
import io
import contextlib

from juez.evaluation.api.schemas import (
    EvaluateRequest,
    EvaluateResponse,
    GenerateCasesRequest,
    GenerateCasesResponse,
    AutogenEvaluateRequest,
    AutogenEvaluateResponse,
    EvaluationPlanRequest,
    EvaluationPlanResponse,
)


load_dotenv()
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")

app = FastAPI(
    title="Lambda AI Judge API — Evaluación de Agentes",
    description=(
        "API de evaluación de agentes (autogen). Endpoints clave:\n"
        "- POST /v1/evaluation-plan — qué reglas y datos se evaluarían (vista previa)\n"
        "- POST /v1/reference-data/ingest — subir información previa (Excel/Word/TXT) como verdad de base\n"
        "- POST /v1/evaluate — evaluar (acepta casos y métricas personalizados)\n"
        "- POST /v1/generate-cases, /v1/auto-evaluate, /v1/autogen/evaluate"
    ),
)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/generate-cases", response_model=GenerateCasesResponse)
def generate_cases_endpoint(payload: GenerateCasesRequest) -> Dict[str, Any]:
    try:
        spec_dict = payload.spec or {}
        if "run_id" not in spec_dict:
            spec_dict = {
                "run_id": payload.run_id or "api-generate-cases",
                "metrics": [],
                "prompt_base": payload.prompt_base or payload.prompt,
            }
        spec = EvaluationSpec(**spec_dict)
        prompt = payload.prompt_base or payload.prompt or spec.prompt_base or ""
        profile = analyze_prompt(prompt)
        cases = generate_autogen_cases(profile, n_cases=payload.n_cases, seed=payload.seed)
        if payload.retrieval_context:
            for tc in cases:
                tc.retrieval_context = [str(x) for x in payload.retrieval_context]
        return {
            "cases": [c.model_dump(mode="json") for c in cases],
            "n_cases": payload.n_cases,
            "seed": payload.seed,
        }
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/reference-data/ingest")
def ingest_reference_data_endpoint(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Recibe y analiza información previa del cliente (Excel/CSV/Word/TXT/JSON).

    Es la "verdad de base" para el Juzgado: el dataset contra el cual se verifica
    que lo que el agente maneja (ej. resultados de tools) sea verídico.

    NO ejecuta nada: solo parsea el archivo a un dataset estructurado y devuelve
    un resumen + una muestra. El dataset completo se usa luego en la evaluación.

    Formatos: .xlsx, .csv, .tsv, .json, .txt, .docx
    """
    from juez.evaluation.reference_data import ParseError, parse_reference_file

    try:
        raw = file.file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="El archivo está vacío.")
        dataset = parse_reference_file(file.filename or "archivo", raw)
        return {
            "resumen": dataset.resumen(),
            "columns": dataset.columns,
            "muestra": dataset.records[:10],
            "n_records": dataset.n_records,
            "notas": dataset.notas,
            "text_preview": dataset.text[:500] if dataset.text else "",
        }
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/evaluation-plan", response_model=EvaluationPlanResponse)
def evaluation_plan_endpoint(payload: EvaluationPlanRequest) -> Dict[str, Any]:
    """Previsualiza QUÉ se le va a evaluar a un agente (reglas + datos).

    Solo-lectura: NO ejecuta al agente ni corre la evaluación. Útil para que el
    consumidor vea, antes de lanzar `/v1/evaluate`, qué reglas (métricas/umbrales)
    se aplicarían y con qué datos (casos sintéticos) se probaría al agente.
    """
    try:
        from collections import Counter

        from juez.evaluation.metric_registry import METRICS

        # 1) PERFIL — qué detectamos del agente a partir de su prompt.
        profile = analyze_prompt(payload.prompt_base)
        perfil = profile.model_dump(mode="json")

        # 2) REGLAS — métricas que se aplicarían (las pedidas, o el catálogo completo).
        nombres = payload.metrics if payload.metrics else list(METRICS.keys())
        reglas: List[Dict[str, Any]] = []
        for nombre in nombres:
            md = METRICS.get(nombre)
            if md is None:
                reglas.append({
                    "name": nombre,
                    "existe": False,
                    "nota": "Métrica desconocida; no está en el catálogo.",
                })
                continue
            reglas.append({
                "name": md.name,
                "tipo": md.kind,
                "umbral": md.default_threshold,
                "requiere_contexto": md.requires_context,
                "requiere_salida_esperada": md.requires_expected_output,
                "existe": True,
            })

        # 3) DATOS — casos sintéticos con los que se evaluaría (opcional).
        datos: List[Dict[str, Any]] = []
        distribucion: Dict[str, int] = {}
        if payload.incluir_casos:
            cases = generate_autogen_cases(profile, n_cases=payload.n_cases, seed=payload.seed)
            datos = [c.model_dump(mode="json") for c in cases]
            tags = [t for c in cases for t in (c.tags or []) if t != "autogen"]
            distribucion = dict(Counter(tags))

        return {
            "perfil_agente": perfil,
            "reglas": reglas,
            "datos": datos,
            "resumen": {
                "n_reglas": len(reglas),
                "n_casos": len(datos),
                "distribucion_por_tag": distribucion,
                "metricas_personalizadas": bool(payload.metrics),
            },
        }
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _normalize_metrics(raw: Any) -> Any:
    """Normaliza las reglas que llegan a /v1/evaluate.

    Permite reenviar las reglas TAL CUAL salen de /v1/evaluation-plan (que usan
    `umbral` y traen campos descriptivos como `tipo`, `existe`, ...) y las
    traduce al formato interno `MetricSpec` (`threshold`, `enabled`, ...). Así el
    flujo editar-reglas -> reenviar -> evaluar funciona directo, y el umbral
    editado por el usuario SÍ se aplica. También acepta el formato MetricSpec
    nativo sin cambios.
    """
    from juez.evaluation.metric_registry import METRICS

    if not isinstance(raw, list):
        return raw
    out: List[Dict[str, Any]] = []
    for m in raw:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("existe") is False:
            continue  # métrica marcada como inexistente en el plan; se ignora
        name = m.get("name")
        if not name:
            continue
        norm: Dict[str, Any] = {"name": name}
        thr = m.get("threshold", m.get("umbral"))
        if thr is None:
            md = METRICS.get(name)
            thr = md.default_threshold if md else 0.8
        norm["threshold"] = thr
        if "enabled" in m:
            norm["enabled"] = bool(m["enabled"])
        if "weight" in m or "peso" in m:
            norm["weight"] = m.get("weight", m.get("peso"))
        cfg = m.get("config")
        if isinstance(cfg, dict):
            norm["config"] = cfg
        out.append(norm)
    return out


@app.post("/v1/evaluate", response_model=EvaluateResponse)
def evaluate_endpoint(payload: EvaluateRequest) -> Dict[str, Any]:
    try:
        spec_dict = payload.spec or {}
        if "run_id" not in spec_dict:
            spec_dict = {
                "run_id": payload.run_id or "api-eval",
                "metrics": payload.metrics or [],
            }
        if payload.prompt_base and "prompt_base" not in spec_dict:
            spec_dict["prompt_base"] = payload.prompt_base
        if payload.metrics and "metrics" not in spec_dict:
            spec_dict["metrics"] = payload.metrics
        # Acepta reglas en el formato de /v1/evaluation-plan (umbral/tipo/...) o
        # en MetricSpec nativo. Hace que el flujo editar -> reenviar funcione.
        if spec_dict.get("metrics"):
            spec_dict["metrics"] = _normalize_metrics(spec_dict["metrics"])
        if payload.config:
            spec_dict.update(payload.config)
        spec = EvaluationSpec(**spec_dict)
        spec.audit_mode = payload.audit_mode
        if payload.agent_ref:
            spec.agent_module = payload.agent_ref.module
            spec.agent_function = payload.agent_ref.function

        cases_payload = payload.cases or payload.spec.get("cases")
        cases: List[TestCase]
        if cases_payload:
            cases = [TestCase(**c) for c in cases_payload]
        else:
            prompt = spec.prompt_base or payload.prompt_base or ""
            profile = analyze_prompt(prompt)
            cases = generate_autogen_cases(profile, n_cases=payload.n_cases, seed=payload.seed)
            if payload.retrieval_context:
                for tc in cases:
                    tc.retrieval_context = [str(x) for x in payload.retrieval_context]

        if payload.mode == "replay":
            last_assistant = ""
            if payload.conversation:
                for turn in reversed(payload.conversation):
                    if turn.role == "assistant":
                        last_assistant = turn.content
                        break

            def _runner(_: TestCase) -> RunnerResult:
                return RunnerResult(
                    output_text=last_assistant,
                    retrieval_context=[str(x) for x in (payload.retrieval_context or [])],
                    latency_ms=0.0,
                )

            engine = EvaluationEngine(spec)
            report = engine.evaluate_run(cases, _runner)
        else:
            engine = EvaluationEngine(spec)
            report = engine.evaluate_run(cases, lambda tc: run_agent(spec, tc))

        forensic = build_forensic_report(report, spec, spec.audit_mode, None)
        report_dict = report.model_dump()
        if hasattr(report.summary, "to_dict"):
            report_dict["summary"] = report.summary.to_dict()
        summary_dict = report_dict.get("summary", {})
        if isinstance(summary_dict, dict):
            summary_dict["forensic_report"] = forensic
            report_dict["summary"] = summary_dict
        report_dict = repair_recursive(report_dict)

        # Guardar artefactos autogen
        run_id = report.summary.run_id
        from pathlib import Path

        out_dir = Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"autogen_{run_id}.json").write_text(
            json.dumps(report_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / f"autogen_{run_id}_cases.json").write_text(
            json.dumps([c.model_dump(mode="json") for c in cases], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        pdf_b64 = None
        if payload.return_pdf:
            buffer = BytesIO()
            render_forensic_pdf(forensic, buffer)
            pdf_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return {"report": report_dict, "pdf_base64": pdf_b64}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/autogen/evaluate", response_model=AutogenEvaluateResponse)
def autogen_evaluate_endpoint(payload: AutogenEvaluateRequest) -> Dict[str, Any]:
    try:
        spec = EvaluationSpec(run_id=f"autogen-{payload.agent_name}", metrics=[], prompt_base=payload.prompt_base)
        spec.audit_mode = payload.audit_mode
        if payload.metrics:
            spec.metrics = []
            from juez.evaluation.metric_registry import METRICS

            for name in payload.metrics:
                metric_def = METRICS.get(name)
                threshold = metric_def.default_threshold if metric_def else 0.8
                spec.metrics.append(
                    MetricSpec(name=name, threshold=threshold, enabled=True, config={})
                )
        spec.audit_mode = payload.audit_mode
        _ = analyze_prompt(payload.prompt_base)
        seed = payload.seed or 7
        nodes = synthesize_context(seed=seed, n_nodes=6)
        cases, context_map = build_autogen_cases(
            prompt_base=payload.prompt_base,
            retrieval_nodes=nodes,
            n_cases=payload.n_cases,
            seed=seed,
        )

        client = AgentHttpClient(
            url=payload.agent_http.url,
            headers=payload.agent_http.headers,
            timeout_ms=payload.agent_http.timeout_ms,
        )

        failures: Dict[str, Dict[str, Any]] = {}

        def _runner(tc: TestCase) -> RunnerResult:
            ctx_nodes = context_map.get(tc.case_id, nodes)
            req_payload = {
                "input": tc.input,
                "prompt_base": payload.prompt_base,
                "retrieval_context": ctx_nodes,
                "case_id": tc.case_id,
            }
            result = client.call(req_payload)
            if result.error:
                failures[tc.case_id] = {
                    "error": result.error,
                    "infra_error": result.infra_error,
                    "model_error": result.model_error,
                }
            return RunnerResult(
                output_text=result.output,
                retrieval_context=[n["text"] for n in ctx_nodes],
                latency_ms=result.latency_ms,
                error=result.error,
            )

        engine = EvaluationEngine(spec)
        with contextlib.redirect_stdout(io.StringIO()):
            report = engine.evaluate_run(cases, _runner)

        # Inyectar métrica de ejecución fallida si aplica
        for case in report.cases:
            if case.case_id in failures:
                err = failures[case.case_id]
                case.metrics.append(
                    MetricResult(
                        name="agent_execution",
                        score=None,
                        threshold=None,
                        success=None,
                        reason=err.get("error"),
                        infra_error=bool(err.get("infra_error")),
                        model_error=bool(err.get("model_error")),
                        skipped=True,
                        skip_reason="infra" if err.get("infra_error") else "model",
                    )
                )

        forensic = build_forensic_report(report, spec, spec.audit_mode, None)
        report_dict = report.model_dump()
        if hasattr(report.summary, "to_dict"):
            report_dict["summary"] = report.summary.to_dict()
        summary_dict = report_dict.get("summary", {})
        if isinstance(summary_dict, dict):
            summary_dict["forensic_report"] = forensic
            report_dict["summary"] = summary_dict
        report_dict = repair_recursive(report_dict)

        pdf_b64 = None
        if payload.return_pdf:
            buffer = BytesIO()
            render_forensic_pdf(forensic, buffer)
            pdf_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return {"report": report_dict, "pdf_base64": pdf_b64}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/auto-evaluate", response_model=AutogenEvaluateResponse)
def auto_evaluate_endpoint(payload: AutoGenRequest) -> Dict[str, Any]:
    try:
        metrics = []
        if payload.metrics:
            from juez.evaluation.metric_registry import METRICS

            for m in payload.metrics:
                name = m.get("name") if isinstance(m, dict) else None
                if not name:
                    continue
                metric_def = METRICS.get(name)
                threshold = (
                    m.get("threshold") if isinstance(m, dict) and m.get("threshold") is not None else None
                )
                if threshold is None:
                    threshold = metric_def.default_threshold if metric_def else 0.8
                metrics.append(
                    MetricSpec(name=name, threshold=threshold, enabled=True, config=m.get("config", {}))
                )
        with contextlib.redirect_stdout(io.StringIO()):
            report = run_auto_eval(
                prompt_base=payload.prompt_base,
                metrics=metrics,
                n_cases=payload.n_cases,
                seed=payload.seed,
                run_id=payload.run_id or "auto-eval",
            )
        forensic = build_forensic_report(report, report.spec, report.spec.audit_mode, None)
        report_dict = report.model_dump()
        if hasattr(report.summary, "to_dict"):
            report_dict["summary"] = report.summary.to_dict()
        summary_dict = report_dict.get("summary", {})
        if isinstance(summary_dict, dict):
            summary_dict["forensic_report"] = forensic
            report_dict["summary"] = summary_dict
        report_dict = repair_recursive(report_dict)
        return {"report": report_dict, "pdf_base64": None}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
