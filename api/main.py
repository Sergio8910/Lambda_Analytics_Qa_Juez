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
from pydantic import ValidationError

from evaluation.report_models import EvaluationSpec, TestCase
from evaluation.core.engine import EvaluationEngine
from evaluation.runner import run_agent
from api.schemas import EvaluateRequest, EvaluateResponse
from api.router_v1 import router as router_v1


logger = logging.getLogger("lambda_judge_api")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

ALLOWED_AGENTS = {
    "agent",
    "evaluation.testdata.mock_strategic_agent",
}


app = FastAPI(
    title="Lambda AI Judge API",
    description=(
        "API del Juez de Lambda Analytics.\n\n"
        "- `/evaluate` y `/health` — API legacy (DeepEval-based)\n"
        "- `/api/v1/*` — API nueva del evaluador multi-agente "
        "(ElevenLabs, n8n, pipeline)"
    ),
    version="1.1.0",
)

# Router v1: endpoints del evaluador nuevo
app.include_router(router_v1)


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
