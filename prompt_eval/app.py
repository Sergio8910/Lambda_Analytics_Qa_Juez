"""App FastAPI standalone de Prompt Eval.

Se levanta en su propio puerto (default 8002), independiente del Juez (8000)
y del Verificador (8001). No comparte estado con esos servicios.

Run:
    uvicorn prompt_eval.app:app --port 8002 --reload
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .router import router
from .settings import settings


logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger("prompt_eval.app")


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Evaluador exhaustivo de system prompts. Recibe un prompt, lo audita "
        "contra ~26 reglas determinísticas + LLM-as-judge opcional, y devuelve "
        "score global, scores por dimensión, hallazgos detallados y "
        "recomendaciones priorizadas.\n\n"
        "**Endpoints:**\n"
        "- `POST /prompt_eval/evaluate` — evalúa un prompt (síncrono)\n"
        "- `GET /prompt_eval/rules` — catálogo de reglas\n"
        "- `GET /health` — ping"
    ),
    version="1.0.0",
)


app.include_router(router)


@app.on_event("startup")
def _on_startup() -> None:
    log.info(
        "prompt_eval.startup app=%s env=%s port=%s llm=%s",
        settings.APP_NAME,
        settings.ENV,
        settings.PORT,
        "on" if settings.OPENAI_API_KEY else "off",
    )
