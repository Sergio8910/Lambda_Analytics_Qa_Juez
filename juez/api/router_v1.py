"""Router v1 — endpoints del evaluador nuevo (ElevenLabs, n8n, pipeline).

Convive con la API legacy en `api/main.py`. Todos los endpoints aquí están
bajo el prefijo `/api/v1`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from juez.api.jobs import get_store
from juez.api.runner import run_elevenlabs_single, run_n8n_single, run_pipeline
from juez.api.schemas_v1 import (
    EvalElevenLabsRequest,
    EvalN8nRequest,
    EvalPipelineRequest,
    HealthResponse,
    JobCreatedResponse,
    JobListResponse,
    JobStatusResponse,
    VerifyObjectivesRequest,
)


router = APIRouter(prefix="/api/v1", tags=["juez-v1"])


# =============================================================================
# HEALTH
# =============================================================================


@router.get("/health", response_model=HealthResponse)
def health() -> Dict[str, Any]:
    """Ping del servicio. Reporta también si los módulos del evaluador cargan."""
    evaluator_available = True
    contra_agente_available = False
    try:
        from juez.api.runner import _load_module  # type: ignore
        _load_module("evaluar_n8n")
        _load_module("evaluar_elevenlabs")
        _load_module("evaluar_pipeline")
    except Exception:
        evaluator_available = False
    try:
        import juez.evaluation.contra_agente.generator  # noqa: F401
        contra_agente_available = True
    except Exception:
        contra_agente_available = False
    return {
        "status": "ok",
        "version": "1.0.0",
        "evaluator_available": evaluator_available,
        "contra_agente_available": contra_agente_available,
    }


# =============================================================================
# CREAR JOBS
# =============================================================================


@router.post("/evaluate/elevenlabs", response_model=JobCreatedResponse, status_code=202)
def evaluate_elevenlabs(req: EvalElevenLabsRequest) -> Dict[str, Any]:
    """Lanza una evaluación de un recurso ElevenLabs.

    `target_id` (o `agent_id` como alias deprecado) acepta:
      - agent_*    : evalúa el agente directo.
      - agtbrch_*  : evalúa el agente bajo esa rama. Si include_n8n_flows=True
        (default), descubre y evalúa también los flujos n8n que el agente
        llama vía tools webhook, retornando un pipeline unificado.
    """
    target = req.resolved_target()
    if not target:
        raise HTTPException(
            status_code=400,
            detail="Debes pasar 'target_id' o 'agent_id' en el request",
        )

    store = get_store()
    job = store.create(kind="elevenlabs", params=req.model_dump(mode="json"))
    job_id = job["job_id"]

    store.run_in_thread(
        job_id,
        run_elevenlabs_single,
        target_id=target,
        include_n8n_flows=req.include_n8n_flows,
        total_conversaciones=req.total_conversaciones,
        concurrencia=req.concurrencia,
        escenarios=req.escenarios,
        openai_key=req.openai_key or "",
        elevenlabs_key=req.elevenlabs_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
    )

    return {
        "job_id": job_id,
        "kind": "elevenlabs",
        "status": "queued",
        "created_at": job["created_at"],
        "poll_url": f"/api/v1/evaluate/{job_id}",
    }


@router.post("/evaluate/n8n", response_model=JobCreatedResponse, status_code=202)
def evaluate_n8n(req: EvalN8nRequest) -> Dict[str, Any]:
    """Lanza una evaluación de un flujo n8n. Retorna inmediatamente con job_id."""
    store = get_store()
    job = store.create(kind="n8n", params=req.model_dump(mode="json"))
    job_id = job["job_id"]

    store.run_in_thread(
        job_id,
        run_n8n_single,
        flow=req.flow.model_dump(mode="json"),
        total_conversaciones=req.total_conversaciones,
        concurrencia=req.concurrencia,
        escenarios=req.escenarios,
        openai_key=req.openai_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
        evaluate_artifact=req.evaluate_artifact,
        artifact_agent_id=req.artifact_agent_id or "",
    )

    return {
        "job_id": job_id,
        "kind": "n8n",
        "status": "queued",
        "created_at": job["created_at"],
        "poll_url": f"/api/v1/evaluate/{job_id}",
    }


@router.post("/evaluate/pipeline", response_model=JobCreatedResponse, status_code=202)
def evaluate_pipeline(req: EvalPipelineRequest) -> Dict[str, Any]:
    """Lanza una evaluación de pipeline completo. Retorna inmediatamente con job_id."""
    if not req.eleven_ids and not req.n8n_flows:
        raise HTTPException(
            status_code=400,
            detail="El pipeline necesita al menos un agente ElevenLabs (eleven_ids) o un flujo n8n (n8n_flows)",
        )

    store = get_store()
    job = store.create(kind="pipeline", params=req.model_dump(mode="json"))
    job_id = job["job_id"]

    store.run_in_thread(
        job_id,
        run_pipeline,
        nombre=req.nombre,
        eleven_ids=req.eleven_ids,
        n8n_flows=[f.model_dump(mode="json") for f in req.n8n_flows],
        total_conversaciones=req.total_conversaciones,
        concurrencia=req.concurrencia,
        escenarios=req.escenarios,
        openai_key=req.openai_key or "",
        elevenlabs_key=req.elevenlabs_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
    )

    return {
        "job_id": job_id,
        "kind": "pipeline",
        "status": "queued",
        "created_at": job["created_at"],
        "poll_url": f"/api/v1/evaluate/{job_id}",
    }


# =============================================================================
# VERIFICACIÓN SINTÉTICA DE OBJETIVOS (síncrono — NO dispara nada)
# =============================================================================


@router.post("/verify/objectives", tags=["juez-v1"])
def verify_objectives_endpoint(req: VerifyObjectivesRequest) -> Dict[str, Any]:
    """Verifica SINTÉTICAMENTE que un flujo n8n cumple sus objetivos.

    No ejecuta ni dispara nada: analiza el JSON del flujo y recorre el grafo en
    seco. Para cada objetivo declarado confirma que existe un nodo que lo cumple,
    alcanzable desde el trigger, habilitado y configurado.

    Es síncrono (el análisis es rápido, no hay I/O externo). Pasa el flujo en
    `flow.json_content`.

    Devuelve veredicto (`cumple` / `cumple_parcial` / `no_cumple`), score global
    y, por objetivo, su status (`cumplido` / `parcial` / `incumplido`) con findings.
    """
    from juez.evaluation.n8n import Objective, verify_objectives

    workflow = req.flow.json_content
    if not workflow:
        raise HTTPException(
            status_code=400,
            detail="El modo sintético requiere el JSON del flujo en 'flow.json_content'.",
        )

    objectives = [Objective(**o.model_dump()) for o in req.objectives]
    report = verify_objectives(workflow, objectives)
    return report.model_dump(mode="json")


# =============================================================================
# CONSULTAR JOBS
# =============================================================================


@router.get("/evaluate/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> Dict[str, Any]:
    """Consulta el estado de un job. Cuando termina, incluye el resultado completo."""
    store = get_store()
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job no encontrado: {job_id}")
    # No exponemos los params al consumidor (ya van scrubbed pero igual no son útiles)
    job.pop("params", None)
    return job


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    kind: Optional[str] = Query(None, description="Filtrar por tipo: elevenlabs, n8n o pipeline"),
) -> Dict[str, Any]:
    """Lista los jobs más recientes (útil para debugging)."""
    store = get_store()
    jobs = store.list(limit=limit, kind=kind)
    for j in jobs:
        j.pop("params", None)
    return {"jobs": jobs, "total": len(jobs)}
