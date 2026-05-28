"""Juez API (operativa)
Ejemplos PowerShell (evita problemas de parsing):
1) Usando Here-String:
@'
{
  "run_id": "auto-1",
  "prompt_base": "Asistente de supermercado",
  "n_cases": 40,
  "seed": 123
}
'@ | Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/auto-evaluate -Method POST -ContentType "application/json"
2) Usando archivo JSON:
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/auto-evaluate -Method POST -ContentType "application/json" -Body (Get-Content -Raw .\request.json)
"""
from __future__ import annotations
import json
import logging
import os
import random
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from evaluation.api.n8n_reviewer_ui import render_n8n_reviewer_html
from evaluation.autogen.context_generator import generate_context_for_case
from evaluation.autogen.prompt_analyzer import analyze_prompt
from evaluation.autogen.case_generator import (
    generate_cases as generate_autogen_cases,
    build_cases as build_autogen_cases,
)
from evaluation.core.engine import EvaluationEngine
from evaluation.judge_engine import _translate_reason as _translate_reason_es
from evaluation.metric_registry import METRICS
from evaluation.n8n import analyze_workflow_with_diagnosis
from evaluation.n8n.models import N8nWorkflowAnalysis
from evaluation.report_models import EvaluationSpec, MetricSpec, TestCase, RunReport
from evaluation.reporting.forensic import build_forensic_report, render_forensic_pdf
from evaluation.runner import run_agent
from evaluation.utils.text_normalization import repair_recursive
load_dotenv()
_LOG_LEVEL = os.getenv("JUEZ_LOG_LEVEL", "INFO").upper()
if _LOG_LEVEL != "DEBUG":
    os.environ.setdefault("OPENAI_LOG", "error")
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
    os.environ.setdefault("DEEPEVAL_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")
logging.basicConfig(level=getattr(logging, _LOG_LEVEL, logging.INFO))
if _LOG_LEVEL != "DEBUG":
    for _noisy_logger in ("openai", "openai._base_client", "httpx", "httpcore"):
        logging.getLogger(_noisy_logger).setLevel(logging.WARNING)
logger = logging.getLogger("juez.api.server")
app = FastAPI(title="Juez API")

try:
    import multipart  # type: ignore  # noqa: F401
    from fastapi import UploadFile, File

    _MULTIPART_AVAILABLE = True
except Exception:
    _MULTIPART_AVAILABLE = False


def require_api_key(x_api_key: str = Header(None)) -> None:
    expected = os.getenv("JUDGE_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="JUDGE_API_KEY no está configurada en el servidor.")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="No autorizado: API Key inválida.")
class EvaluateRequest(BaseModel):
    run_id: str
    spec: Optional[Dict[str, Any]] = None
    spec_path: Optional[str] = None
    cases: List[Dict[str, Any]] = Field(default_factory=list)
    agent_type: Optional[str] = None
    scorecard_config: Optional[Dict[str, Any]] = None
    audit_mode: Optional[str] = None
    pdf_out: Optional[str] = None
    concurrency: Optional[int] = 3
    save_outputs: bool = True
    model_config = {"extra": "forbid"}
class AutoEvaluateRequest(BaseModel):
    run_id: str
    prompt_base: str
    n_cases: int = 40
    seed: int = 123
    metrics: Optional[List[str]] = None
    evaluation_profile: Optional[str] = None
    rag_file: Optional[str] = None
    rag_id: Optional[str] = None
    focus: Optional[str] = None
    agent_type: str = "chat_agent"
    audit_mode: str = "balanced"
    include_pdf: bool = False
    save_outputs: bool = True
    include_summary: bool = True
    summary_model: Optional[str] = None
    summary_max_tokens: int = 700
    summary_temperature: float = 0.2
    return_summary: bool = False
    concurrency: int = 3
    run_prompt_fix_demo: bool = True
    prompt_fix_demo_max_cases: int = 0
    prompt_fix_demo_max_iterations: int = 5
    model_config = {"extra": "forbid"}
class GenerateCasesRequest(BaseModel):
    prompt_base: str
    n_cases: int = 40
    seed: int = 123
    model_config = {"extra": "forbid"}
class GenerateCasesResponse(BaseModel):
    cases: List[Dict[str, Any]]
    n_cases: int
    seed: Optional[int] = None
    model_config = {"extra": "forbid"}


class UploadRagResponse(BaseModel):
    rag_id: str
    path: str

    model_config = {"extra": "forbid"}


class N8nAnalyzeRequest(BaseModel):
    workflow: Any
    include_graph: bool = True
    include_diagnosis: bool = True
    diagnosis_mode: Literal["auto", "llm", "fallback"] = "auto"
    diagnosis_model: Optional[str] = None
    diagnosis_max_tokens: int = 900
    diagnosis_temperature: float = 0.1

    model_config = {"extra": "forbid"}


class N8nAnalyzeResponse(BaseModel):
    analysis: N8nWorkflowAnalysis
    api_meta: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/ui/n8n-reviewer", response_class=HTMLResponse, include_in_schema=False)
def n8n_reviewer_ui() -> str:
    return render_n8n_reviewer_html()


if _MULTIPART_AVAILABLE:

    @app.post("/v1/upload-rag", response_model=UploadRagResponse)
    def upload_rag_endpoint(
        file: UploadFile = File(...), _: None = Depends(require_api_key)
    ) -> Dict[str, Any]:
        filename = _sanitize_filename(file.filename or "rag")
        ext = Path(filename).suffix.lower()
        if ext not in {".json", ".txt"}:
            raise HTTPException(status_code=400, detail="Extension invalida. Use .json o .txt.")
        data = file.file.read()
        if data is None:
            raise HTTPException(status_code=400, detail="Archivo vacio.")
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (maximo 5 MB).")
        out_dir = Path("RAGs")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = _unique_filename(out_dir, filename)
        out_path = out_dir / out_name
        out_path.write_bytes(data)
        return {"rag_id": out_name, "path": f"RAGs/{out_name}"}
else:

    @app.post("/v1/upload-rag")
    def upload_rag_endpoint(_: None = Depends(require_api_key)) -> Dict[str, Any]:
        raise HTTPException(
            status_code=503,
            detail="python-multipart no esta instalado. Instala con: pip install python-multipart",
        )
@app.post("/v1/generate-cases", response_model=GenerateCasesResponse)
def generate_cases_endpoint(
    payload: GenerateCasesRequest, _: None = Depends(require_api_key)
) -> Dict[str, Any]:
    cases, _ = _generate_cases(payload.prompt_base, payload.n_cases, payload.seed, metric_names=None)
    return {
        "cases": [c.model_dump(mode="json") for c in cases],
        "n_cases": payload.n_cases,
        "seed": payload.seed,
    }


@app.post("/v1/n8n/analyze", response_model=N8nAnalyzeResponse)
def analyze_n8n_workflow_endpoint(
    payload: N8nAnalyzeRequest, _: None = Depends(require_api_key)
) -> Dict[str, Any]:
    workflow = _coerce_n8n_workflow(payload.workflow)
    analysis, warnings = _run_n8n_analysis_with_optional_diagnosis(
        workflow=workflow,
        include_graph=payload.include_graph,
        include_diagnosis=payload.include_diagnosis,
        diagnosis_mode=payload.diagnosis_mode,
        diagnosis_model=payload.diagnosis_model,
        diagnosis_max_tokens=payload.diagnosis_max_tokens,
        diagnosis_temperature=payload.diagnosis_temperature,
    )
    return ensure_json_safe({"analysis": analysis.model_dump(mode="json"), "api_meta": _build_api_meta(warnings)})


if _MULTIPART_AVAILABLE:

    @app.post("/v1/n8n/analyze-file", response_model=N8nAnalyzeResponse)
    def analyze_n8n_workflow_file_endpoint(
        file: UploadFile = File(...),
        include_graph: bool = True,
        include_diagnosis: bool = True,
        diagnosis_mode: Literal["auto", "llm", "fallback"] = "auto",
        diagnosis_model: Optional[str] = None,
        diagnosis_max_tokens: int = 900,
        diagnosis_temperature: float = 0.1,
        _: None = Depends(require_api_key),
    ) -> Dict[str, Any]:
        if Path(file.filename or "workflow.json").suffix.lower() != ".json":
            raise HTTPException(status_code=400, detail="El archivo debe tener extensión .json.")
        raw_bytes = file.file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Archivo vacío.")
        try:
            workflow = _coerce_n8n_workflow(raw_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="No se pudo decodificar el archivo como UTF-8.") from exc
        analysis, warnings = _run_n8n_analysis_with_optional_diagnosis(
            workflow=workflow,
            include_graph=include_graph,
            include_diagnosis=include_diagnosis,
            diagnosis_mode=diagnosis_mode,
            diagnosis_model=diagnosis_model,
            diagnosis_max_tokens=diagnosis_max_tokens,
            diagnosis_temperature=diagnosis_temperature,
        )
        return ensure_json_safe({"analysis": analysis.model_dump(mode="json"), "api_meta": _build_api_meta(warnings)})
else:

    @app.post("/v1/n8n/analyze-file")
    def analyze_n8n_workflow_file_endpoint(_: None = Depends(require_api_key)) -> Dict[str, Any]:
        raise HTTPException(
            status_code=503,
            detail="python-multipart no esta instalado. Instala con: pip install python-multipart",
        )


@app.post("/v1/evaluate")
def evaluate_endpoint(payload: EvaluateRequest, _: None = Depends(require_api_key)) -> Dict[str, Any]:
    if not payload.spec and not payload.spec_path:
        raise HTTPException(status_code=400, detail="spec o spec_path es requerido")
    if not payload.cases:
        raise HTTPException(status_code=400, detail="cases es requerido")
    spec = _load_spec(payload.spec, payload.spec_path, payload.run_id)
    if payload.agent_type:
        spec.agent_type = payload.agent_type
    if payload.scorecard_config:
        spec.scorecard_weights = payload.scorecard_config.get("weights", spec.scorecard_weights)
        spec.scorecard_gates = payload.scorecard_config.get("gates", spec.scorecard_gates)
    if payload.audit_mode:
        spec.audit_mode = payload.audit_mode
    if payload.concurrency:
        spec.max_concurrency = max(1, int(payload.concurrency))
    cases = [TestCase(**c) for c in payload.cases]
    report = run_engine(spec, cases)
    report_dict = _report_to_dict(report, spec)
    warnings: List[str] = []
    if payload.pdf_out:
        _maybe_render_pdf(report_dict, payload.pdf_out, warnings)
    api_meta = _build_api_meta(warnings)
    response = {"report": report_dict, "api_meta": api_meta}
    response = ensure_json_safe(response)
    if payload.save_outputs:
        _save_outputs(payload.run_id, report_dict, response)
    return response
@app.post("/v1/auto-evaluate")
def auto_evaluate_endpoint(payload: AutoEvaluateRequest, _: None = Depends(require_api_key)) -> Dict[str, Any]:
    prompt_base = payload.prompt_base
    rag_file = payload.rag_id or payload.rag_file
    rag_chunks = _load_rag_context(rag_file) if rag_file else []
    has_rag = bool(rag_chunks)
    if payload.metrics:
        metric_names = payload.metrics
        gating_metrics = None
    else:
        metric_names, gating_metrics = _profile_to_metrics(
            payload.evaluation_profile, has_rag=has_rag, prompt_base=prompt_base
        )
    cases, spec = _auto_build_spec_and_cases(
        run_id=payload.run_id,
        prompt_base=prompt_base,
        n_cases=payload.n_cases,
        seed=payload.seed,
        metric_names=metric_names,
        agent_type=payload.agent_type,
        audit_mode=payload.audit_mode,
        rag_chunks=rag_chunks,
        focus=payload.focus,
    )
    if gating_metrics is not None:
        spec.gating_metrics = gating_metrics
    spec.max_concurrency = max(1, int(payload.concurrency or 1))
    report = run_engine(spec, cases)
    report_dict = _report_to_dict(report, spec)
    if payload.run_prompt_fix_demo:
        fix_demo = _run_prompt_fix_demo(
            spec=spec,
            cases=cases,
            report_dict=report_dict,
            max_cases=payload.prompt_fix_demo_max_cases,
            max_iterations=payload.prompt_fix_demo_max_iterations,
        )
        summary_dict = report_dict.get("summary", {})
        if isinstance(summary_dict, dict):
            summary_dict["prompt_fix_demo"] = fix_demo
            report_dict["summary"] = summary_dict
    warnings: List[str] = []
    if payload.include_pdf:
        _maybe_render_pdf(report_dict, f"outputs/{payload.run_id}.pdf", warnings)
    summary_text: Optional[str] = None
    try:
        summary_text = _build_narrative_summary_llm(
            report_dict=report_dict,
            model=payload.summary_model or os.getenv("SUMMARY_MODEL") or os.getenv("EVAL_MODEL") or "gpt-4o-mini",
            max_tokens=payload.summary_max_tokens,
            temperature=payload.summary_temperature,
        )
        logger.info("Resumen narrativo generado para run_id=%s", payload.run_id)
    except Exception as exc:
        warnings.append(f"Resumen LLM no generado: {exc}")
        summary_text = _build_narrative_summary_fallback(report_dict)
    api_meta = _build_api_meta(warnings)
    if summary_text:
        summary_dict = report_dict.get("summary", {})
        if isinstance(summary_dict, dict):
            summary_dict["narrative_summary"] = summary_text
            report_dict["summary"] = summary_dict
    response = {"report": report_dict, "api_meta": api_meta}
    if summary_text and payload.return_summary:
        response["narrative_summary"] = summary_text
    response = ensure_json_safe(response)
    if payload.save_outputs:
        _save_outputs(payload.run_id, report_dict, response, summary_text)
    return response


def _coerce_n8n_workflow(raw_workflow: Any) -> Dict[str, Any]:
    if isinstance(raw_workflow, dict):
        workflow = raw_workflow
    elif isinstance(raw_workflow, str):
        try:
            workflow = json.loads(raw_workflow)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"workflow no es JSON válido: {exc.msg}") from exc
    else:
        raise HTTPException(status_code=400, detail="workflow debe ser un objeto JSON o un string JSON.")

    if not isinstance(workflow, dict):
        raise HTTPException(status_code=400, detail="workflow debe resolver a un objeto JSON.")
    if "nodes" not in workflow:
        raise HTTPException(
            status_code=400,
            detail="workflow no contiene la clave 'nodes'. Exporta el workflow completo desde n8n.",
        )
    if "connections" not in workflow:
        raise HTTPException(
            status_code=400,
            detail="workflow no contiene la clave 'connections'. Exporta el workflow completo desde n8n.",
        )
    return workflow


def _run_n8n_analysis_with_optional_diagnosis(
    *,
    workflow: Dict[str, Any],
    include_graph: bool,
    include_diagnosis: bool,
    diagnosis_mode: Literal["auto", "llm", "fallback"],
    diagnosis_model: Optional[str],
    diagnosis_max_tokens: int,
    diagnosis_temperature: float,
) -> tuple[N8nWorkflowAnalysis, List[str]]:
    if include_diagnosis:
        return analyze_workflow_with_diagnosis(
            workflow,
            include_graph=include_graph,
            diagnosis_mode=diagnosis_mode,
            diagnosis_model=diagnosis_model,
            diagnosis_max_tokens=diagnosis_max_tokens,
            diagnosis_temperature=diagnosis_temperature,
        )
    analysis, warnings = analyze_workflow_with_diagnosis(
        workflow,
        include_graph=include_graph,
        diagnosis_mode="fallback",
        diagnosis_model=diagnosis_model,
        diagnosis_max_tokens=diagnosis_max_tokens,
        diagnosis_temperature=diagnosis_temperature,
    )
    return analysis.model_copy(update={"diagnosis": None}), warnings


def run_engine(spec: EvaluationSpec, cases: List[TestCase]) -> RunReport:
    engine = EvaluationEngine(spec)
    return engine.evaluate_run(cases, lambda tc: run_agent(spec, tc), dump_normalized_run=True)
def _load_spec(spec_dict: Optional[Dict[str, Any]], spec_path: Optional[str], run_id: str) -> EvaluationSpec:
    if spec_path:
        path = Path(spec_path)
        if not path.exists():
            raise HTTPException(status_code=400, detail="spec_path no existe")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        data["run_id"] = run_id
        return EvaluationSpec(**data)
    spec_dict = spec_dict or {}
    spec_dict["run_id"] = run_id
    return EvaluationSpec(**spec_dict)
def _build_metric_specs(names: List[str]) -> List[MetricSpec]:
    specs: List[MetricSpec] = []
    for name in names:
        metric_def = METRICS.get(name)
        threshold = metric_def.default_threshold if metric_def else 0.8
        specs.append(MetricSpec(name=name, threshold=threshold, enabled=True, config={}))
    return specs
def _default_metric_names(prompt_base: str) -> List[str]:
    profile = analyze_prompt(prompt_base)
    names = [
        "answer_relevancy",
        "instruction_adherence",
        "task_success",
        "task_success_deterministic",
        "unsupported_claims",
        "format_compliance",
        "latency_budget",
    ]
    if profile.context_dependency:
        names.extend(["faithfulness", "contextual_precision", "hallucination"])
    return names
def _profile_to_metrics(
    profile_name: Optional[str], has_rag: bool, prompt_base: str
) -> tuple[List[str], Optional[List[str]]]:
    if not profile_name:
        if has_rag:
            metrics = ["faithfulness", "contextual_precision", "hallucination", "unsupported_claims"]
            gating = ["faithfulness", "hallucination"]
            return metrics, gating
        return _default_metric_names(prompt_base), None
    name = profile_name.lower().strip()
    if name == "rag_quality":
        metrics = ["faithfulness", "contextual_precision", "hallucination", "unsupported_claims"]
        gating = ["faithfulness", "hallucination"]
        return metrics, gating
    if name == "instruction_strict":
        metrics = ["instruction_adherence", "format_compliance", "task_success"]
        gating = ["instruction_adherence", "format_compliance"]
        return metrics, gating
    if name == "accuracy_only":
        metrics = ["task_success_deterministic", "unsupported_claims"]
        gating = ["task_success_deterministic"]
        return metrics, gating
    if name == "safety_integrity":
        metrics = ["unsupported_claims", "hallucination"]
        gating = ["unsupported_claims"]
        return metrics, gating
    if name == "latency_sensitive":
        metrics = ["latency_budget", "task_success"]
        gating = ["latency_budget"]
        return metrics, gating
    if name == "balanced":
        return _default_metric_names(prompt_base), None
    # fallback: si no se reconoce, usar default
    return _default_metric_names(prompt_base), None
def _auto_build_spec_and_cases(
    run_id: str,
    prompt_base: str,
    n_cases: int,
    seed: int,
    metric_names: List[str],
    agent_type: str,
    audit_mode: str,
    rag_chunks: Optional[List[str]] = None,
    focus: Optional[str] = None,
) -> tuple[List[TestCase], EvaluationSpec]:
    spec = EvaluationSpec(
        run_id=run_id,
        metrics=_build_metric_specs(metric_names),
        prompt_base=prompt_base,
        agent_type=agent_type,
    )
    spec.audit_mode = audit_mode
    spec.gating_metrics = [
        m
        for m in [
            "task_success_deterministic",
            "unsupported_claims",
            "format_compliance",
            "latency_budget",
        ]
        if m in metric_names
    ]
    cases, _ = _generate_cases(prompt_base, n_cases, seed, metric_names, rag_chunks, focus)
    return cases, spec
def _generate_cases(
    prompt_base: str,
    n_cases: int,
    seed: int,
    metric_names: Optional[List[str]],
    rag_chunks: Optional[List[str]] = None,
    focus: Optional[str] = None,
) -> tuple[List[TestCase], Dict[str, List[Dict[str, Any]]]]:
    n_cases = max(1, min(50, n_cases))
    profile = analyze_prompt(prompt_base)
    use_rag = bool(rag_chunks) or profile.context_dependency or (
        metric_names
        and any(m in {"faithfulness", "contextual_precision", "hallucination"} for m in metric_names)
    )
    if use_rag and rag_chunks:
        retrieval_nodes = [
            {"id": f"RAG-{i+1}", "text": str(c), "source": "rag"} for i, c in enumerate(rag_chunks)
        ]
        cases, context_map = build_autogen_cases(
            prompt_base=prompt_base,
            retrieval_nodes=retrieval_nodes,
            n_cases=n_cases,
            seed=seed,
        )
        for case in cases:
            if "autogen" not in case.tags:
                case.tags.append("autogen")
            if focus:
                case.input = f"{case.input} Enfoque: {focus}."
        return cases, context_map
    cases = generate_autogen_cases(profile, n_cases=n_cases, seed=seed)
    context_map: Dict[str, List[Dict[str, Any]]] = {}
    for case in cases:
        if focus:
            case.input = f"{case.input} Enfoque: {focus}."
        if use_rag:
            ctx_nodes = generate_context_for_case(profile, case, seed=seed)
            case.retrieval_context = [n["text"] for n in ctx_nodes]
            if "contexto" not in (case.input or "").lower():
                case.input = f"{case.input} Usa el contexto disponible."
            context_map[case.case_id] = ctx_nodes
    return cases, context_map
def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 3]
def _auto_select_rag(prompt_base: str) -> Optional[str]:
    base = Path("RAGs")
    if not base.exists():
        return None
    prompt_tokens = set(_tokenize(prompt_base))
    if not prompt_tokens:
        return None
    best_name = None
    best_score = 0
    for path in base.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".json", ".txt"}:
            continue
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        text_block = raw
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and "chunks" in data:
                    text_block = " ".join(str(c) for c in data.get("chunks") or [])
                elif isinstance(data, list):
                    text_block = " ".join(str(c) for c in data)
            except Exception:
                text_block = raw
        score = len(prompt_tokens & set(_tokenize(text_block)))
        if score > best_score:
            best_score = score
            best_name = path.name
    return best_name
def _load_rag_context(rag_file: str) -> List[str]:
    base = Path("RAGs").resolve()
    path = (base / rag_file).resolve()
    if not str(path).startswith(str(base)):
        raise HTTPException(status_code=400, detail="rag_file fuera de la carpeta RAGs.")
    if not path.exists():
        raise HTTPException(status_code=400, detail="rag_file no existe.")
    if path.suffix.lower() == ".txt":
        lines = [l.strip() for l in path.read_text(encoding="utf-8-sig").splitlines()]
        chunks = [l for l in lines if l]
        return [str(c) for c in chunks]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and "chunks" in data:
        chunks = data.get("chunks") or []
    elif isinstance(data, list):
        chunks = data
    else:
        raise HTTPException(status_code=400, detail="rag_file inv?lido.")
    return [str(c) for c in chunks]
def _report_to_dict(report: RunReport, spec: EvaluationSpec) -> Dict[str, Any]:
    forensic = build_forensic_report(report, spec, spec.audit_mode, None)
    report_dict = report.model_dump()
    if hasattr(report.summary, "to_dict"):
        report_dict["summary"] = report.summary.to_dict()
    summary_dict = report_dict.get("summary", {})
    if isinstance(summary_dict, dict):
        summary_dict["forensic_report"] = forensic
        report_dict["summary"] = summary_dict
    report_dict = repair_recursive(report_dict)
    return report_dict


def _sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
    if not clean:
        clean = "rag"
    return clean


def _unique_filename(base_dir: Path, name: str) -> str:
    candidate = name
    if not (base_dir / candidate).exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{stem}_{ts}{suffix}"


def _format_score(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.3f}"
    return "n/a"


def _format_percent(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value) * 100:.1f}%"
    return "n/a"


def _truncate_text(text: Any, max_chars: int = 380) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_chars:
        return raw
    return f"{raw[:max_chars].rstrip()}..."


def _severity_rank(severity: Any) -> int:
    value = str(severity or "").lower().strip()
    if value == "alta":
        return 3
    if value == "media":
        return 2
    if value == "baja":
        return 1
    return 0


def _english_ratio(text: str) -> float:
    tokens = [t for t in re.findall(r"[a-zA-Z]+", text.lower()) if t]
    if not tokens:
        return 0.0
    common_en = {
        "the",
        "because",
        "fails",
        "fail",
        "does",
        "not",
        "response",
        "output",
        "question",
        "address",
        "irrelevant",
        "score",
        "english",
        "spanish",
        "and",
        "with",
        "for",
        "this",
        "that",
    }
    hits = sum(1 for t in tokens if t in common_en)
    return hits / max(1, len(tokens))


def _to_spanish_text(text: Any) -> str:
    raw = repair_recursive(str(text or "")).strip()
    if not raw:
        return "Sin evidencia disponible."
    translated = _translate_reason_es(raw)
    clean = repair_recursive(str(translated or raw)).strip()
    if _english_ratio(clean) > 0.2:
        return "La métrica devolvió una razón en inglés. Se omite el texto original."
    return clean


def _metric_label(name: Any) -> str:
    metric = str(name or "").strip()
    mapping = {
        "answer_relevancy": "Relevancia de respuesta",
        "instruction_adherence": "Adherencia a instrucciones",
        "task_success": "Éxito de tarea",
        "task_success_deterministic": "Éxito de tarea determinístico",
        "unsupported_claims": "Afirmaciones sin respaldo",
        "faithfulness": "Fidelidad al contexto",
        "contextual_precision": "Precisión contextual",
        "hallucination": "Alucinación por contradicción",
        "format_compliance": "Cumplimiento de formato",
        "latency_budget": "Presupuesto de latencia",
    }
    if metric in mapping:
        return f"{mapping[metric]} ({metric})"
    return metric or "métrica_desconocida"


def _extract_primary_fail_reasons(case: Dict[str, Any]) -> List[str]:
    feedback = case.get("feedback", {}) or {}
    overall = feedback.get("overall", {}) if isinstance(feedback, dict) else {}
    reasons = overall.get("primary_fail_reasons", []) if isinstance(overall, dict) else []
    if isinstance(reasons, list) and reasons:
        return [_to_spanish_text(r) for r in reasons if str(r).strip()]

    result: List[str] = []
    for metric in (case.get("metrics", []) or []):
        if metric.get("success") is False:
            reason = metric.get("reason_es") or metric.get("reason")
            if reason:
                result.append(f"{_metric_label(metric.get('name'))}: {_to_spanish_text(reason)}")
        if len(result) >= 3:
            break
    return result


def _detect_redundancy_signals(cases: List[Dict[str, Any]]) -> List[str]:
    hits: List[str] = []
    for case in cases:
        case_id = str(case.get("case_id", ""))
        candidate_texts: List[str] = []
        for metric in (case.get("metrics", []) or []):
            candidate_texts.append(str(metric.get("reason_es") or metric.get("reason") or ""))
        for reason in _extract_primary_fail_reasons(case):
            candidate_texts.append(reason)
        joined = " ".join(candidate_texts).lower()
        if any(token in joined for token in ("redundan", "repetitiv", "relleno", "verbose", "verbosity")):
            hits.append(case_id)
    return hits


def _build_pretty_summary_txt(
    report_dict: Dict[str, Any], narrative_text: Optional[str]
) -> str:
    report_dict = repair_recursive(report_dict)
    summary = report_dict.get("summary", {}) or {}
    spec = report_dict.get("spec", {}) or {}
    cases = report_dict.get("cases", []) or []
    exec_sum = summary.get("executive_summary", {}) or {}

    run_id = str(summary.get("run_id") or spec.get("run_id") or "sin-run-id")
    total_cases = summary.get("total_cases")
    passed_cases = summary.get("passed_cases")
    failed_cases = summary.get("failed_cases")
    pass_rate = summary.get("pass_rate")
    reliability = summary.get("reliability_score")
    verdict = exec_sum.get("verdict") or "SIN VEREDICTO"
    risk = exec_sum.get("risk_level") or "SIN RIESGO"

    metrics_enabled: List[str] = []
    for metric in (spec.get("metrics", []) or []):
        if not isinstance(metric, dict):
            continue
        if metric.get("enabled", True):
            name = str(metric.get("name", "")).strip()
            if name:
                metrics_enabled.append(_metric_label(name))
    gating_metrics = [
        _metric_label(m) for m in (spec.get("gating_metrics", []) or []) if str(m).strip()
    ]
    eval_profile = str(
        spec.get("evaluation_profile")
        or exec_sum.get("audit_mode")
        or spec.get("audit_mode")
        or "default"
    )

    by_metric_failures = summary.get("by_metric_failures", {}) or {}
    top_metrics = sorted(by_metric_failures.items(), key=lambda x: x[1], reverse=True)[:5]
    by_tag_failures = summary.get("by_tag_failures", {}) or {}
    top_tags = sorted(by_tag_failures.items(), key=lambda x: x[1], reverse=True)[:3]
    redundancy_cases = _detect_redundancy_signals(cases)

    failed_case_rows = [c for c in cases if c.get("passed") is False]
    failed_case_rows = sorted(
        failed_case_rows,
        key=lambda c: (
            -_severity_rank(c.get("severity")),
            float((c.get("scorecard", {}) or {}).get("overall_score") or 1.0),
            str(c.get("case_id", "")),
        ),
    )
    failed_case_rows = failed_case_rows[:5]

    lines: List[str] = []
    lines.append("=== RESUMEN EJECUTIVO ===")
    lines.append(f"- Run ID: {run_id}")
    lines.append(f"- Fecha: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- Casos totales: {total_cases} | OK: {passed_cases} | Fallidos: {failed_cases}")
    lines.append(f"- Pass rate: {_format_percent(pass_rate)}")
    lines.append(f"- Reliability: {_format_score(reliability)}")
    lines.append(f"- Veredicto: {verdict}")
    lines.append(f"- Riesgo: {risk}")
    lines.append("")

    lines.append("=== QUE SE EVALUO ===")
    lines.append(
        f"- Metricas habilitadas: {', '.join(metrics_enabled) if metrics_enabled else 'Sin evidencia disponible.'}"
    )
    lines.append(
        f"- Gating metrics activas: {', '.join(gating_metrics) if gating_metrics else 'Sin evidencia disponible.'}"
    )
    lines.append(f"- Perfil de evaluacion: {eval_profile}")
    lines.append("")

    lines.append("=== TOP HALLAZGOS ===")
    if top_metrics:
        lines.append("- Top metricas con mas fallos:")
        for name, count in top_metrics:
            lines.append(f"  - {_metric_label(name)}: {count}")
    else:
        lines.append("- Top metricas con mas fallos: Sin evidencia disponible.")
    if top_tags:
        lines.append("- Top tags mas problematicos:")
        for name, count in top_tags:
            lines.append(f"  - {name}: {count}")
    else:
        lines.append("- Top tags mas problematicos: Sin evidencia disponible.")
    if redundancy_cases:
        lines.append(f"- Senal de redundancia detectada en casos: {', '.join(redundancy_cases[:8])}")
    else:
        lines.append("- Senal de redundancia: Sin evidencia disponible.")
    lines.append("")

    lines.append("=== CASOS CRITICOS (TOP 3-5) ===")
    if not failed_case_rows:
        lines.append("- Sin evidencia disponible.")
    for case in failed_case_rows:
        case_id = str(case.get("case_id", "N/A"))
        severity = str(case.get("severity", "N/A"))
        tags = ", ".join([str(t) for t in (case.get("tags", []) or [])])
        lines.append(f"- Caso: {case_id} | Severity: {severity} | Tags: {tags or 'sin-tags'}")
        lines.append(f"  Input: {_truncate_text(case.get('input'), 420)}")
        fail_reasons = _extract_primary_fail_reasons(case)
        if fail_reasons:
            lines.append("  Por que fallo:")
            for reason in fail_reasons[:3]:
                lines.append(f"    - {reason}")
        else:
            lines.append("  Por que fallo: Sin evidencia disponible.")
        failed_metrics = [m for m in (case.get("metrics", []) or []) if m.get("success") is False]
        if failed_metrics:
            lines.append("  Metricas fallidas:")
            for metric in failed_metrics[:6]:
                reason = _to_spanish_text(metric.get("reason_es") or metric.get("reason"))
                lines.append(
                    f"    - {_metric_label(metric.get('name'))}: score={_format_score(metric.get('score'))} | reason={_truncate_text(reason, 240)}"
                )
        else:
            lines.append("  Metricas fallidas: Sin evidencia disponible.")
    lines.append("")

    lines.append("=== FIX DE PROMPT VALIDADO ===")
    prompt_fix_demo = summary.get("prompt_fix_demo", {}) if isinstance(summary, dict) else {}
    demos = prompt_fix_demo.get("demonstrations", []) if isinstance(prompt_fix_demo, dict) else []
    if not demos:
        lines.append("- Sin evidencia disponible.")
    else:
        lines.append(f"- Casos re-ejecutados: {prompt_fix_demo.get('cases_attempted', len(demos))}")
        lines.append(f"- Casos mejorados: {prompt_fix_demo.get('cases_improved', 0)}")
        for demo in demos:
            lines.append(f"- Caso: {demo.get('case_id', 'N/A')}")
            patch = demo.get("prompt_patch", {}) if isinstance(demo, dict) else {}
            rules = patch.get("rules", []) if isinstance(patch, dict) else []
            if rules:
                lines.append("  Reglas aplicadas:")
                for rule in rules[:5]:
                    lines.append(f"    - {rule}")
            improvements = demo.get("improvements", []) if isinstance(demo, dict) else []
            if improvements:
                lines.append("  Mejora observada:")
                for imp in improvements[:5]:
                    metric_name = _metric_label(imp.get("metric"))
                    lines.append(
                        f"    - {metric_name}: delta={_format_score(imp.get('delta'))} "
                        f"({imp.get('before_score')} -> {imp.get('after_score')})"
                    )
            else:
                lines.append("  Mejora observada: Sin evidencia disponible.")
            prompt_example = str(demo.get("example_prompt_fixed", "")).strip()
            if prompt_example:
                lines.append("  Prompt corregido (ejemplo):")
                lines.append(f"  {_truncate_text(prompt_example, 460)}")
    lines.append("")

    lines.append("=== RECOMENDACIONES PRIORITARIAS ===")
    recommendations = list(summary.get("recommendations", []) or [])
    if not recommendations:
        recommendations = list(exec_sum.get("recommended_actions", []) or [])
    if recommendations:
        for idx, rec in enumerate(recommendations[:9]):
            if idx < 3:
                priority = "P0"
            elif idx < 6:
                priority = "P1"
            else:
                priority = "P2"
            lines.append(f"- {priority}: {rec}")
    else:
        lines.append("- Sin evidencia disponible.")
    lines.append("")

    lines.append("=== VEREDICTO FINAL ===")
    narrative = (narrative_text or "").strip()
    if not narrative:
        narrative = str(exec_sum.get("human_summary", "")).strip() or _build_narrative_summary_fallback(report_dict)
    if _english_ratio(narrative) > 0.2:
        narrative = str(exec_sum.get("human_summary", "")).strip() or "La narrativa no pudo generarse completamente en español."
    lines.append(_to_spanish_text(narrative))
    lines.append(f"Veredicto: {verdict}")
    return repair_recursive("\n".join(lines))
def _save_outputs(
    run_id: str,
    report_dict: Dict[str, Any],
    response: Dict[str, Any],
    summary_text: Optional[str] = None,
) -> None:
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{run_id}.json"
    report_path.write_text(
        json.dumps(report_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    final_summary = _build_pretty_summary_txt(
        report_dict=report_dict,
        narrative_text=summary_text,
    )
    summary_path = out_dir / f"{run_id}_summary.txt"
    summary_path.write_text(repair_recursive(final_summary), encoding="utf-8")
    created = [str(report_path), str(summary_path)]
    logger.info(
        "Se creo en la carpeta %s el reporte para run_id=%s. Archivos: %s",
        str(out_dir),
        run_id,
        ", ".join(created),
    )
def _maybe_render_pdf(report_dict: Dict[str, Any], out_path: str, warnings: List[str]) -> None:
    try:
        forensic = report_dict.get("summary", {}).get("forensic_report", {})
        render_forensic_pdf(forensic, out_path)
    except Exception as exc:
        warnings.append(f"PDF no generado: {exc}")
def _build_api_meta(warnings: List[str]) -> Dict[str, Any]:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "version": os.getenv("JUEZ_VERSION", "unknown"),
        "warnings": warnings,
    }
def ensure_json_safe(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BaseModel):
        return ensure_json_safe(obj.model_dump(mode="json"))
    if is_dataclass(obj):
        return ensure_json_safe(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): ensure_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [ensure_json_safe(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float))


def _failed_metrics_snapshot(case_dict: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
    return [
        {
            "name": m.get("name"),
            "score": m.get("score"),
            "reason_es": m.get("reason_es") or m.get("reason"),
        }
        for m in (case_dict.get("metrics", []) or [])
        if m.get("success") is False
    ][:limit]


def _count_failed_metrics(case_dict: Dict[str, Any]) -> int:
    return len([m for m in (case_dict.get("metrics", []) or []) if m.get("success") is False])


def _compute_metric_improvements(
    before_case: Dict[str, Any], after_case: Dict[str, Any]
) -> List[Dict[str, Any]]:
    before_metrics = {
        m.get("name"): m for m in (before_case.get("metrics", []) or []) if m.get("name")
    }
    after_metrics = {
        m.get("name"): m for m in (after_case.get("metrics", []) or []) if m.get("name")
    }
    improvements: List[Dict[str, Any]] = []

    for name, before in before_metrics.items():
        if before.get("success") is not False:
            continue
        after = after_metrics.get(name)
        if not after:
            continue
        before_score = before.get("score")
        after_score = after.get("score")
        delta = None
        if _is_number(before_score) and _is_number(after_score):
            delta = float(after_score) - float(before_score)
        became_success = before.get("success") is False and after.get("success") is True
        if became_success or (delta is not None and delta > 0.0):
            improvements.append(
                {
                    "metric": name,
                    "before_score": before_score,
                    "after_score": after_score,
                    "delta": delta,
                    "before_success": before.get("success"),
                    "after_success": after.get("success"),
                }
            )
    return improvements


def _build_prompt_patch_for_case(
    case_dict: Dict[str, Any],
    attempt_number: int,
    already_applied_rules: Optional[List[str]] = None,
) -> Dict[str, Any]:
    metrics = case_dict.get("metrics", []) or []
    failed_metrics = [m for m in metrics if m.get("success") is False]
    failed_names = {str(m.get("name")) for m in failed_metrics if m.get("name")}

    patch_lines: List[str] = []
    if {"answer_relevancy", "task_success", "task_success_deterministic"} & failed_names:
        patch_lines.append(
            "Responde exactamente la solicitud y cubre todas las subpreguntas en el mismo orden."
        )
    if {"instruction_adherence", "format_compliance"} & failed_names:
        patch_lines.append(
            "Responde 100% en espanol, sin Markdown, sin listas con guiones y en formato directo."
        )
    if {"unsupported_claims", "faithfulness", "hallucination", "contextual_precision"} & failed_names:
        patch_lines.append(
            "Usa solo informacion del contexto; si falta un dato, responde exactamente: Lo siento, no tengo acceso a esa informacion ahora."
        )

    if attempt_number >= 2:
        patch_lines.append(
            "Antes de responder, valida que cada parte pedida por el usuario tenga una respuesta explicita."
        )
    if attempt_number >= 3:
        patch_lines.append(
            "Si el dato existe en el contexto, esta prohibido responder con rechazo generico."
        )
    if attempt_number >= 4:
        patch_lines.append(
            "Evita relleno y frases rituales; entrega solo informacion util y verificable."
        )
    if attempt_number >= 5:
        patch_lines.append(
            "Haz una verificacion final: cobertura completa, formato correcto y cero invenciones."
        )
    if not patch_lines:
        patch_lines.append(
            "Se mas preciso, evita redundancia y responde solo lo pedido por el usuario."
        )

    all_rules = list(already_applied_rules or [])
    for line in patch_lines:
        if line not in all_rules:
            all_rules.append(line)

    patch_text = (
        f"Reglas de mejora detectadas por el Juez (iteracion {attempt_number}):\n"
        + "\n".join(f"- {line}" for line in patch_lines)
    )
    merged_patch_text = (
        "Reglas consolidadas aplicadas en esta validacion:\n"
        + "\n".join(f"- {line}" for line in all_rules)
    )
    return {
        "patch_lines": patch_lines,
        "all_rules": all_rules,
        "patch_text": patch_text,
        "merged_patch_text": merged_patch_text,
    }


def _run_prompt_fix_demo(
    spec: EvaluationSpec,
    cases: List[TestCase],
    report_dict: Dict[str, Any],
    max_cases: int,
    max_iterations: int,
) -> Dict[str, Any]:
    case_map = {c.case_id: c for c in cases}
    failed_cases = [c for c in (report_dict.get("cases", []) or []) if c.get("passed") is False]
    requested_cases = int(max_cases or 0)
    if requested_cases <= 0:
        max_cases = len(failed_cases)
    else:
        max_cases = min(len(failed_cases), requested_cases)
    max_iterations = max(1, min(8, int(max_iterations or 1)))
    selected = failed_cases[:max_cases]

    demonstrations: List[Dict[str, Any]] = []
    improved_count = 0
    solved_count = 0

    for case_dict in selected:
        case_id = str(case_dict.get("case_id", ""))
        tc = case_map.get(case_id)
        if not tc:
            continue

        spec_data = spec.model_dump(mode="json")
        original_prompt = str(spec_data.get("prompt_base", "") or "")
        original_failed_count = _count_failed_metrics(case_dict)
        applied_rules: List[str] = []
        current_case_ref = case_dict
        attempts: List[Dict[str, Any]] = []
        winning_attempt: Optional[Dict[str, Any]] = None
        winning_case: Optional[Dict[str, Any]] = None
        winning_failed_count = 10**9

        for attempt_number in range(1, max_iterations + 1):
            patch_info = _build_prompt_patch_for_case(
                current_case_ref,
                attempt_number=attempt_number,
                already_applied_rules=applied_rules,
            )
            applied_rules = list(patch_info["all_rules"])
            patched_prompt = (original_prompt + "\n\n" + patch_info["merged_patch_text"]).strip()

            attempt_spec_data = dict(spec_data)
            attempt_spec_data["run_id"] = f"{spec.run_id}-fix-{case_id}-it{attempt_number}"
            attempt_spec_data["prompt_base"] = patched_prompt
            fixed_spec = EvaluationSpec(**attempt_spec_data)
            fixed_spec.max_concurrency = 1
            fixed_spec.fail_fast = False
            fixed_spec.llm_fail_fast_on_infra = False

            fixed_report = run_engine(fixed_spec, [tc])
            fixed_case = fixed_report.cases[0].model_dump(mode="json") if fixed_report.cases else {}
            improvements = _compute_metric_improvements(case_dict, fixed_case)
            failed_count = _count_failed_metrics(fixed_case)
            passed_now = fixed_case.get("passed") is True

            attempt_data = {
                "attempt": attempt_number,
                "passed": passed_now,
                "failed_metrics_count": failed_count,
                "improvements": improvements,
                "prompt_patch": {
                    "mode": "append",
                    "rules": patch_info["patch_lines"],
                    "text": patch_info["patch_text"],
                },
                "example_prompt_fixed": patched_prompt,
            }
            attempts.append(attempt_data)

            if passed_now:
                winning_attempt = attempt_data
                winning_case = fixed_case
                winning_failed_count = failed_count
                break

            if failed_count < winning_failed_count:
                winning_attempt = attempt_data
                winning_case = fixed_case
                winning_failed_count = failed_count

            current_case_ref = fixed_case if fixed_case else current_case_ref

        if winning_case is None:
            winning_case = {}
        if winning_attempt is None:
            winning_attempt = {
                "attempt": 0,
                "passed": False,
                "failed_metrics_count": original_failed_count,
                "improvements": [],
                "prompt_patch": {"mode": "append", "rules": [], "text": ""},
                "example_prompt_fixed": original_prompt,
            }

        solved_case = winning_case.get("passed") is True
        case_improved = solved_case or bool(winning_attempt.get("improvements")) or (
            winning_failed_count < original_failed_count
        )
        if case_improved:
            improved_count += 1
        if solved_case:
            solved_count += 1

        demonstrations.append(
            {
                "case_id": case_id,
                "status": "solucionado" if solved_case else "no_solucionado_en_max_iteraciones",
                "improved": case_improved,
                "solved": solved_case,
                "attempts_count": len(attempts),
                "before": {
                    "passed": case_dict.get("passed"),
                    "failed_metrics": _failed_metrics_snapshot(case_dict),
                },
                "after": {
                    "passed": winning_case.get("passed"),
                    "failed_metrics": _failed_metrics_snapshot(winning_case),
                },
                "prompt_patch": winning_attempt.get("prompt_patch"),
                "improvements": winning_attempt.get("improvements"),
                "example_prompt_fixed": winning_attempt.get("example_prompt_fixed"),
                "attempts": attempts,
            }
        )

    return {
        "enabled": True,
        "max_cases": max_cases,
        "max_iterations": max_iterations,
        "cases_attempted": len(selected),
        "cases_improved": improved_count,
        "cases_solved": solved_count,
        "demonstrations": demonstrations,
    }


def _build_narrative_summary_llm(
    report_dict: Dict[str, Any],
    model: str,
    max_tokens: int,
    temperature: float,
) -> str:
    from openai import OpenAI
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY no está configurada.")
    summary = report_dict.get("summary", {}) or {}
    cases = report_dict.get("cases", []) or []
    forensic = summary.get("forensic_report", {}) or {}
    rep_failed = forensic.get("failure_analysis", {}).get("representative_failed_cases", []) or []
    def _case_brief(case: Dict[str, Any]) -> Dict[str, Any]:
        metrics = case.get("metrics", []) or []
        failed = [
            {
                "name": m.get("name"),
                "score": m.get("score"),
                "reason": m.get("reason_es") or m.get("reason"),
            }
            for m in metrics
            if m.get("success") is False
        ]
        return {
            "case_id": case.get("case_id"),
            "tags": case.get("tags", []),
            "severity": case.get("severity"),
            "failed_metrics": failed[:5],
        }
    top_failed = rep_failed[:5]
    top_failed_brief = [_case_brief(c) for c in top_failed] if top_failed else []
    passed_cases = [c for c in cases if c.get("passed") is True]
    passed_brief = [_case_brief(c) for c in passed_cases[:5]]
    prompt = (
        "Eres un auditor senior de calidad de IA. Genera un informe narrativo en español, "
        "detallado y estructurado con estas secciones:\n"
        "1) Evaluación Global\n"
        "2) Análisis de Casos Específicos\n"
        "3) Recomendaciones Generales\n\n"
        "Debe incluir: qué se evaluó, por qué falló o pasó, redundancias detectadas, "
        "casos destacados y recomendaciones concretas de mejora del prompt o del agente.\n"
        "No uses JSON en la respuesta.\n"
        "Al final agrega una línea exacta con el formato:\n"
        "Veredicto: <texto>\n"
        "Usa el veredicto de executive_summary si está disponible.\n\n"
        f"Resumen:\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"Casos fallidos representativos:\n{json.dumps(top_failed_brief, ensure_ascii=False)}\n\n"
        f"Casos destacados (pasados):\n{json.dumps(passed_brief, ensure_ascii=False)}\n"
    )
    client = OpenAI(timeout=30, max_retries=0)
    if hasattr(client, "responses"):
        resp = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        text = getattr(resp, "output_text", "") or ""
        return text.strip()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (resp.choices[0].message.content if resp.choices else "").strip()
def _build_narrative_summary_fallback(report_dict: Dict[str, Any]) -> str:
    summary = report_dict.get("summary", {}) or {}
    exec_sum = summary.get("executive_summary", {}) or {}
    pass_rate = summary.get("pass_rate")
    failed_cases = summary.get("failed_cases")
    total_cases = summary.get("total_cases")
    verdict = exec_sum.get("verdict") or "SIN VEREDICTO"
    top_metrics = summary.get("by_metric_failures", {}) or {}
    top_list = sorted(top_metrics.items(), key=lambda x: x[1], reverse=True)[:5]
    lines = [
        "Evaluacion Global:",
        f"Se evaluaron {total_cases} casos; {failed_cases} fallaron. Pass rate: {pass_rate}.",
    ]
    if top_list:
        top_str = ", ".join([f"{k} ({v})" for k, v in top_list])
        lines.append(f"Las metricas con mas fallos fueron: {top_str}.")
    lines.extend(
        [
            "Analisis de Casos Especificos:",
            "Se detectaron fallos en la cobertura de instrucciones o uso de contexto en casos representativos.",
            "Recomendaciones Generales:",
            "Refuerza la claridad del prompt, prioriza preguntas aclaratorias en ambiguedad y evita inventar datos.",
            f"Veredicto: {verdict}",
        ]
    )
    prompt_fix_demo = summary.get("prompt_fix_demo", {}) if isinstance(summary, dict) else {}
    if isinstance(prompt_fix_demo, dict) and prompt_fix_demo.get("demonstrations"):
        lines.append("")
        lines.append("Validacion de prompt corregido:")
        lines.append(
            f"Se probaron {prompt_fix_demo.get('cases_attempted')} caso(s) fallidos con un patch automatico de prompt; "
            f"mejoraron {prompt_fix_demo.get('cases_improved')}."
        )
        first_demo = (prompt_fix_demo.get("demonstrations") or [None])[0]
        if isinstance(first_demo, dict):
            lines.append(f"Caso ejemplo: {first_demo.get('case_id')}.")
            patch = first_demo.get("prompt_patch", {})
            if isinstance(patch, dict):
                text = str(patch.get("text", "")).strip()
                if text:
                    lines.append("Patch sugerido y validado:")
                    lines.append(text)
    return "\n".join(lines)
