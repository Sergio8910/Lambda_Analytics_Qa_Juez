"""Router v1 — endpoints del evaluador nuevo (ElevenLabs, n8n, pipeline).

Convive con la API legacy en `api/main.py`. Todos los endpoints aquí están
bajo el prefijo `/api/v1`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from juez.api.jobs import get_store
from juez.api.reference_store import get_reference_store
from juez.api.runner import run_elevenlabs_single, run_n8n_single, run_pipeline, run_proyecto
from juez.api.schemas_v1 import (
    EvalElevenLabsRequest,
    EvalN8nRequest,
    EvalPipelineRequest,
    EvalProyectoRequest,
    EvaluationPlanRequest,
    EvaluationPlanResponse,
    HealthResponse,
    JobCreatedResponse,
    JobListResponse,
    JobStatusResponse,
    N8nFailurePayload,
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
# PLAN DE EVALUACIÓN (solo-lectura)
# =============================================================================


@router.post("/evaluation-plan", response_model=EvaluationPlanResponse)
def evaluation_plan(payload: EvaluationPlanRequest) -> Dict[str, Any]:
    """Previsualiza QUÉ se le va a evaluar a un agente (reglas + datos).

    Solo-lectura: NO ejecuta al agente ni corre la evaluación. Útil para que el
    consumidor vea, antes de lanzar `/api/v1/evaluate`, qué reglas (métricas/umbrales)
    se aplicarían y con qué datos (casos sintéticos) se probaría al agente.
    """
    try:
        from collections import Counter

        from juez.evaluation.autogen.prompt_analyzer import analyze_prompt
        from juez.evaluation.autogen.case_generator import generate_cases as generate_autogen_cases
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        modo_ejecucion=req.modo_ejecucion,
        reference_dataset_id=req.reference_dataset_id,
        openai_key=req.openai_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
        evaluate_artifact=req.evaluate_artifact,
        artifact_agent_id=req.artifact_agent_id or "",
        modo_qa=req.modo_qa,
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
        modo_ejecucion=req.modo_ejecucion,
        reference_dataset_id=req.reference_dataset_id,
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


@router.post("/evaluate/proyecto", response_model=JobCreatedResponse, status_code=202)
def evaluate_proyecto(req: EvalProyectoRequest) -> Dict[str, Any]:
    """Evaluación UNIFICADA de un proyecto → contrato firme.

    Corre La Colmena (construcción: seguridad, flujos, objetivos, prompt) y,
    opcionalmente, las conversaciones, y consolida todo en un solo JSON estable
    con `score`, `estado`, `problemas[]` y `mejoras[]` (la mejora del prompt trae
    `antes`/`despues` real, lista para aplicar). Retorna de inmediato con job_id.
    """
    if not req.prompt.strip() and not req.eleven_ids and not req.n8n_flows:
        raise HTTPException(
            status_code=400,
            detail="El proyecto necesita al menos 'prompt', 'eleven_ids' o 'n8n_flows'.",
        )

    store = get_store()
    job = store.create(kind="proyecto", params=req.model_dump(mode="json"))
    job_id = job["job_id"]

    store.run_in_thread(
        job_id,
        run_proyecto,
        nombre=req.nombre,
        prompt=req.prompt,
        eleven_ids=req.eleven_ids,
        n8n_flows=[f.model_dump(mode="json") for f in req.n8n_flows],
        total_conversaciones=req.total_conversaciones,
        concurrencia=req.concurrencia,
        escenarios=req.escenarios,
        incluir_conversaciones=req.incluir_conversaciones,
        incluir_dinamicas=req.incluir_dinamicas,
        modo_ejecucion=req.modo_ejecucion,
        reglas_negocio=req.reglas_negocio,
        objetivos=req.objetivos,
        reference_dataset_id=req.reference_dataset_id,
        openai_key=req.openai_key or "",
        elevenlabs_key=req.elevenlabs_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
    )

    return {
        "job_id": job_id,
        "kind": "proyecto",
        "status": "queued",
        "created_at": job["created_at"],
        "poll_url": f"/api/v1/evaluate/{job_id}",
    }


# =============================================================================
# EVALUACIÓN 24/7 — recibe el fallo de n8n y genera el reporte
# =============================================================================


@router.post("/evaluate/on-failure", response_model=JobCreatedResponse, status_code=202)
def evaluate_on_failure(payload: N8nFailurePayload) -> Dict[str, Any]:
    """Recibe el payload del Error Workflow de n8n cuando un flujo falla.

    Lo llama el nodo HTTP del flujo 'Servicio_de_notificaciones_general'
    (errorTrigger). El Juez descarga el flujo que falló, lo evalúa (análisis
    estático + QA de artefacto sintético, sin disparar nada) y genera un reporte
    TXT con el contexto del fallo. Responde de inmediato (202) para no bloquear
    el flujo de notificaciones; el reporte se guarda en outputs/.
    """
    from juez.api.failure_eval import run_on_failure

    store = get_store()
    raw = payload.model_dump(mode="json")
    job = store.create(kind="failure", params=raw)
    job_id = job["job_id"]
    store.run_in_thread(job_id, run_on_failure, payload=raw)

    return {
        "job_id": job_id,
        "kind": "failure",
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
# DATOS DE REFERENCIA — subir una vez, reusar en muchas corridas/monitores
# =============================================================================


@router.post("/reference-data/ingest", tags=["juez-v1"])
def ingest_reference_data(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Sube y persiste un dataset de referencia (información real para pruebas).

    Formatos: .xlsx, .csv, .tsv, .json, .txt, .docx. A diferencia del endpoint
    legacy, este SÍ persiste el resultado y devuelve un `id` reusable en
    `EvalProyectoRequest.reference_dataset_id` para futuras corridas/monitores.

    JSON especial: si el archivo es un único objeto JSON que contiene el
    marcador `{{JUEZ_MENSAJE}}`, se guarda como `payload_template` — un
    ejemplo REAL del sobre que espera el webhook (ej. WhatsApp Business API),
    para que las conversaciones de prueba lo disparen con la forma correcta
    en vez de un payload genérico que el flujo no reconoce.
    """
    from juez.evaluation.reference_data.parser import ParseError, parse_reference_file

    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Archivo vacío.")
    try:
        dataset = parse_reference_file(file.filename or "archivo", raw)
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {exc}") from exc

    entry = get_reference_store().save(dataset)
    return {"id": entry["id"], "created_at": entry["created_at"], "resumen": dataset.resumen()}


@router.get("/reference-data/{dataset_id}", tags=["juez-v1"])
def get_reference_data(dataset_id: str) -> Dict[str, Any]:
    """Consulta un dataset de referencia previamente ingerido por su id."""
    entry = get_reference_store().get_entry(dataset_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Dataset no encontrado: {dataset_id}")
    return entry


@router.get("/reference-data", tags=["juez-v1"])
def list_reference_data(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Lista los datasets de referencia más recientes (para elegir por id)."""
    items = get_reference_store().list(limit=limit)
    return {"items": items, "total": len(items)}


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
