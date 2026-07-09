from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv

# Carga variables de entorno antes de importar DeepEval.
load_dotenv()

# Desactiva telemetría antes de cargar DeepEval (si aplica).
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DEEPEVAL_TELEMETRY", "false")

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from juez.evaluation.report_models import EvaluationSpec, TestCase
from juez.evaluation.core.engine import EvaluationEngine
from juez.evaluation.runner import run_agent
from juez.api.schemas import EvaluateRequest, EvaluateResponse
from juez.api.router_v1 import router as router_v1
from prompt_eval.router import router as prompt_eval_router


logger = logging.getLogger("lambda_judge_api")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

ALLOWED_AGENTS = {
    "agent",
    "evaluation.testdata.mock_strategic_agent",
}


app = FastAPI(
    title="Lambda AI Judge API — Jobs Async (ElevenLabs/n8n/Pipeline)",
    description=(
        "API del Juez de Lambda Analytics.\n\n"
        "- `/evaluate` y `/health` — API legacy (DeepEval-based)\n"
        "- `/api/v1/*` — API nueva del evaluador multi-agente "
        "(ElevenLabs, n8n, pipeline)"
    ),
    version="1.1.0",
)

# CORS — el frontend (Gamma UI) llama a esta API directamente desde el navegador.
# Sin esto, el preflight OPTIONS devuelve 405 y la UI marca "Sin conexión".
# Orígenes configurables vía JUEZ_CORS_ORIGINS (lista separada por comas); el
# fallback cubre los dominios conocidos de Gamma + localhost para desarrollo.
_cors_env = os.getenv("JUEZ_CORS_ORIGINS", "")
if _cors_env.strip():
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    _cors_origins = [
        "https://gamma.lambdaanalytics.co",
        "http://gamma.lambdaanalytics.co",
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:8080",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router v1: endpoints del evaluador nuevo
app.include_router(router_v1)

# App de "Evaluación de Agentes" (evaluation.api.app) montada como sub-app bajo
# /eval. Expone en el MISMO proceso/URL desplegado los endpoints que antes solo
# vivían en el servicio del puerto 8000 (no levantado en prod): en particular
#   - POST /eval/v1/generate-scenarios   (genera escenarios desde el negocio)
#   - POST /eval/v1/evaluate             (evalúa aceptando reglas editadas del plan)
#   - POST /eval/v1/evaluation-plan, /eval/v1/generate-cases, etc.
# Se monta como sub-app (en vez de copiar routers) para no duplicar código ni sus
# dependencias. Una sub-app montada NO hereda el CORS del padre, así que se le
# añade su propio CORSMiddleware con los mismos orígenes.
try:
    from fastapi.middleware.cors import CORSMiddleware as _CORS
    from juez.evaluation.api.app import app as _evaluation_app

    _evaluation_app.add_middleware(
        _CORS,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/eval", _evaluation_app)
    logger.info("Sub-app de evaluación montada en /eval")
except Exception as _exc:  # el montaje no debe tumbar el arranque del Juez
    logger.warning("No se pudo montar la sub-app de evaluación en /eval: %s", _exc)

# Router de Prompt Eval (evaluación de un system prompt en aislamiento).
# Expone POST /prompt_eval/evaluate y GET /prompt_eval/rules. Se omite su
# ruta /health para no duplicar el /health que esta app ya define abajo
# (FastAPI registraría dos rutas iguales y la segunda quedaría sombreada).
for _r in prompt_eval_router.routes:
    if getattr(_r, "path", None) != "/health":
        app.router.routes.append(_r)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.getenv("JUDGE_API_KEY", "")
    if not expected or not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _validate_agent_whitelist(agent_module: str) -> None:
    if agent_module not in ALLOWED_AGENTS:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate(
    payload: EvaluateRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> Dict[str, Any]:
    _require_api_key(x_api_key)
    try:
        _validate_agent_whitelist(str(payload.spec.get("agent_module", "")))
        spec = EvaluationSpec(**payload.spec)
        if payload.audit_mode:
            spec.audit_mode = "enterprise" if payload.audit_mode == "enterprise" else "balanced"
        cases = [TestCase(**c) for c in payload.cases]
        engine = EvaluationEngine(spec)
        t0 = time.monotonic()
        report = engine.evaluate_run(cases, lambda tc: run_agent(spec, tc))
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        summary = report.summary.to_dict() if hasattr(report.summary, "to_dict") else report.summary.model_dump(mode="json")
        cases_out: List[Dict[str, Any]] = [c.model_dump(mode="json") for c in report.cases]
        exec_summary = summary.get("executive_summary") if isinstance(summary, dict) else None
        verdict = None
        if isinstance(exec_summary, dict):
            verdict = exec_summary.get("verdict")
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_module": spec.agent_module,
            "audit_mode": spec.audit_mode,
            "run_id": spec.run_id,
            "pass_rate": summary.get("pass_rate") if isinstance(summary, dict) else None,
            "verdict": verdict,
            "execution_time_ms": round(elapsed_ms, 2),
        }
        logger.info("judge_request %s", log_payload)
        return {"summary": summary, "cases": cases_out}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
