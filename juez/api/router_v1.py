"""Router v1 — endpoints del evaluador nuevo (ElevenLabs, n8n, pipeline).

Convive con la API legacy en `api/main.py`. Todos los endpoints aquí están
bajo el prefijo `/api/v1`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from juez.api.jobs import get_store
from juez.api.monitor_store import get_monitor_store
from juez.api.reference_store import get_reference_store
from juez.api.runner import (
    run_certificacion,
    run_elevenlabs_single,
    run_n8n_single,
    run_pipeline,
    run_proyecto,
    run_proyecto_self_heal,
)
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
    MonitorCreateRequest,
    MonitorHistorialResponse,
    MonitorListResponse,
    MonitorResponse,
    MonitorUpdateRequest,
    N8nFailurePayload,
    CertificacionRequest,
    PathCoverageRequest,
    SelfHealRequest,
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
        cubrir_caminos=req.cubrir_caminos,
        conversaciones_reales=req.conversaciones_reales,
        conversaciones_sinteticas=req.conversaciones_sinteticas,
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


@router.post("/proyecto/self-heal", response_model=JobCreatedResponse, status_code=202)
def self_heal_proyecto(req: SelfHealRequest) -> Dict[str, Any]:
    """Propone Y VERIFICA fixes reales del proyecto (antes/después), sin tocar
    nada real: corre el self-heal autónomo de La Colmena sobre la MISMA
    reconstrucción temporal efímera que usa /evaluate/proyecto (nunca un repo
    real), aplica cada fix candidato, re-evalúa, y solo se queda con los que
    mejoran el score sin agregar críticos (si no, rollback automático).

    A diferencia de la mejora de prompt de /evaluate/proyecto (una reescritura
    con LLM), este motor itera hallazgo por hallazgo con fixers deterministas
    + fixer genérico opcional, y verifica cada cambio re-evaluando el proyecto
    antes de aceptarlo. El resultado trae `propuestas[]` (antes/después real
    por archivo) para que un humano en Gamma decida aplicarlas al agente real.
    """
    if not req.prompt.strip() and not req.n8n_flows:
        raise HTTPException(
            status_code=400,
            detail="El self-heal necesita al menos 'prompt' o 'n8n_flows'.",
        )

    store = get_store()
    job = store.create(kind="self_heal", params=req.model_dump(mode="json"))
    job_id = job["job_id"]

    store.run_in_thread(
        job_id,
        run_proyecto_self_heal,
        nombre=req.nombre,
        prompt=req.prompt,
        n8n_flows=[f.model_dump(mode="json") for f in req.n8n_flows],
        reglas_negocio=req.reglas_negocio,
        objetivos=req.objetivos,
        min_confidence=req.min_confidence,
        max_iterations=req.max_iterations,
        max_lines_per_fix=req.max_lines_per_fix,
        enable_generic_fixer=req.enable_generic_fixer,
        openai_key=req.openai_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
    )

    return {
        "job_id": job_id,
        "kind": "self_heal",
        "status": "queued",
        "created_at": job["created_at"],
        "poll_url": f"/api/v1/evaluate/{job_id}",
    }


@router.post("/proyecto/certificar", response_model=JobCreatedResponse, status_code=202)
def certificar_proyecto_endpoint(req: CertificacionRequest) -> Dict[str, Any]:
    """Ciclo completo de La Colmena: analiza -> evalúa (todas las dimensiones) ->
    construye fixes (self-heal) -> re-evalúa -> itera hasta CONVERGER, y emite un
    CERTIFICADO consciente de cobertura (solo certifica 'todo bien' sobre lo que
    realmente se evaluó).

    El resultado trae: veredicto (CERTIFICADO / CERTIFICADO_CON_OBSERVACIONES /
    NO_CERTIFICADO), score inicial->final, las rondas del ciclo, por qué paró
    (convergencia / sin críticos / máx rondas), hallazgos restantes, cobertura y
    lo que requiere revisión humana. Opera sobre un proyecto temporal efímero.
    """
    if not req.prompt.strip() and not req.n8n_flows:
        raise HTTPException(
            status_code=400,
            detail="La certificación necesita al menos 'prompt' o 'n8n_flows'.",
        )

    store = get_store()
    job = store.create(kind="certificacion", params=req.model_dump(mode="json"))
    job_id = job["job_id"]

    store.run_in_thread(
        job_id,
        run_certificacion,
        nombre=req.nombre,
        prompt=req.prompt,
        n8n_flows=[f.model_dump(mode="json") for f in req.n8n_flows],
        reglas_negocio=req.reglas_negocio,
        objetivos=req.objetivos,
        max_rondas=req.max_rondas,
        incluir_dinamicas=req.incluir_dinamicas,
        auto_fix=req.auto_fix,
        min_confidence=req.min_confidence,
        max_lines_per_fix=req.max_lines_per_fix,
        enable_generic_fixer=req.enable_generic_fixer,
        presupuesto_tokens=req.presupuesto_tokens,
        presupuesto_usd=req.presupuesto_usd,
        openai_key=req.openai_key or "",
        n8n_api_key=req.n8n_api_key or "",
        n8n_base_url=req.n8n_base_url or "",
    )

    return {
        "job_id": job_id,
        "kind": "certificacion",
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


@router.post("/analyze/path-coverage", tags=["juez-v1"])
def analyze_path_coverage_endpoint(req: PathCoverageRequest) -> Dict[str, Any]:
    """Cobertura de CAMINOS de un flujo n8n (100% estático, sin tokens).

    Resuelve el problema de que todas las pruebas tomen la misma rama: enumera
    los caminos del grafo, extrae la condición de cada rama (IF/Switch/Filter) y
    la clasifica en CONTROLABLE_POR_INPUT (se puede forzar armando el payload) o
    DEPENDE_DE_EJECUCION (depende de la salida de un HTTP/Code/AI previo). Además
    sintetiza, por camino, el payload que fuerza sus ramas controlables.

    Pasa el flujo en `flow.json_content`.
    Devuelve: nodos de ramificación con sus condiciones, caminos, resumen de
    controlabilidad, nodos no cubiertos, e inputs sugeridos por camino.
    """
    from juez.evaluation.n8n.path_coverage import (
        analizar_caminos,
        cobertura_combinada,
        cobertura_de_nodos,
        generar_escenarios_por_rama,
        sintetizar_inputs_por_camino,
    )

    workflow = req.flow.json_content
    if not workflow:
        raise HTTPException(
            status_code=400,
            detail="Se requiere el JSON del flujo en 'flow.json_content'.",
        )
    return {
        "analisis_caminos": analizar_caminos(workflow),
        "inputs_por_camino": sintetizar_inputs_por_camino(workflow),
        "cobertura_de_nodos": cobertura_de_nodos(workflow),
        "cobertura_combinada": cobertura_combinada(workflow),
        "escenarios_por_rama": generar_escenarios_por_rama(workflow),
    }


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
# MONITOREO PROGRAMADO — evaluaciones recurrentes con historial
# =============================================================================


@router.post("/monitors", response_model=MonitorResponse, status_code=201)
def create_monitor(req: MonitorCreateRequest) -> Dict[str, Any]:
    """Crea un monitor programado sobre un proyecto (prompt/agentes/reglas de
    negocio/escenarios/datos de referencia, igual que /evaluate/proyecto) que
    se ejecuta solo con la frecuencia indicada. Cada corrida corre la Colmena
    moderna (evaluate_project_path) vía run_proyecto y queda en el historial
    del monitor, con el delta de score contra la corrida anterior.
    """
    if not req.prompt.strip() and not req.eleven_ids and not req.n8n_flows:
        raise HTTPException(
            status_code=400,
            detail="El monitor necesita al menos 'prompt', 'eleven_ids' o 'n8n_flows'.",
        )
    monitor = get_monitor_store().create(req.model_dump(mode="json"))
    return monitor


@router.get("/monitors", response_model=MonitorListResponse)
def list_monitors(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    """Lista los monitores programados, más recientes primero."""
    items = get_monitor_store().list(limit=limit)
    return {"monitors": items, "total": len(items)}


@router.get("/monitors/{monitor_id}", response_model=MonitorResponse)
def get_monitor(monitor_id: str) -> Dict[str, Any]:
    """Consulta un monitor por id, incluyendo su historial completo."""
    monitor = get_monitor_store().get(monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail=f"Monitor no encontrado: {monitor_id}")
    return monitor


@router.patch("/monitors/{monitor_id}", response_model=MonitorResponse)
def update_monitor(monitor_id: str, req: MonitorUpdateRequest) -> Dict[str, Any]:
    """Pausa o reactiva un monitor (`active`). El scheduler ignora los monitores pausados."""
    cambios = req.model_dump(exclude_none=True)
    monitor = get_monitor_store().update(monitor_id, **cambios) if cambios else get_monitor_store().get(monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail=f"Monitor no encontrado: {monitor_id}")
    return monitor


@router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: str) -> None:
    """Elimina un monitor y su historial."""
    if not get_monitor_store().delete(monitor_id):
        raise HTTPException(status_code=404, detail=f"Monitor no encontrado: {monitor_id}")


@router.get("/monitors/{monitor_id}/historial", response_model=MonitorHistorialResponse)
def get_monitor_historial(monitor_id: str, limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Historial de corridas de un monitor, más reciente primero."""
    store = get_monitor_store()
    if not store.get(monitor_id):
        raise HTTPException(status_code=404, detail=f"Monitor no encontrado: {monitor_id}")
    hist = store.historial(monitor_id, limit=limit)
    return {"monitor_id": monitor_id, "historial": hist, "total": len(hist)}


@router.post("/monitors/{monitor_id}/run-now", status_code=202)
def run_monitor_now(monitor_id: str) -> Dict[str, Any]:
    """Dispara una corrida inmediata del monitor, fuera de su horario
    programado. Responde de inmediato; el resultado aparece en el historial
    (GET /monitors/{id}/historial) cuando termine.
    """
    import threading

    from juez.api.scheduler import ejecutar_monitor

    store = get_monitor_store()
    monitor = store.get(monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail=f"Monitor no encontrado: {monitor_id}")
    threading.Thread(target=ejecutar_monitor, args=(monitor,), daemon=True).start()
    return {"monitor_id": monitor_id, "status": "queued", "historial_url": f"/api/v1/monitors/{monitor_id}/historial"}


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
