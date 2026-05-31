"""FastAPI app del Verificador (standalone).

Corre en su propio proceso y puerto (default 8001) — NO comparte uvicorn
con la API del Juez. Esto preserva el aislamiento total que pidió el
usuario.

Levantar:
    uvicorn verificador.app:app --port 8001 --reload

Smoke:
    curl http://localhost:8001/health
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .schemas import HealthResponse
from .settings import settings

# Side-effect imports: cada uno registra sí mismo en su registry
# (inspectors, sources, clientes) al ser importado. Esto debe ocurrir
# ANTES de aceptar requests.
from .inspectors import pdf as _pdf_inspector            # noqa: F401  — auto-registro
from .sources import drive as _drive_source              # noqa: F401  — auto-registro
from .sources import inline as _inline_source            # noqa: F401  — auto-registro
from .clientes import abad as _abad_client               # noqa: F401  — auto-registro
from .clientes import abad_synthetic as _abad_synthetic  # noqa: F401  — auto-registro


_LOG_LEVEL = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
)
log = logging.getLogger("verificador")


app = FastAPI(
    title="Verificador Lambda Analytics",
    description=(
        "Servicio post-ejecución que audita artefactos (PDFs, imágenes) "
        "generados por flows productivos. SEPARADO del Juez."
    ),
    version="0.1.0",
    # No exponer docs en producción accidentalmente — solo en dev
    docs_url="/docs",
    redoc_url=None,
)


# Registrar el router al cargar el módulo (antes de que la app reciba requests)
from .router import router as _verificador_router  # noqa: E402
app.include_router(_verificador_router)


@app.on_event("startup")
async def _startup() -> None:
    log.info(
        "Verificador arrancando. config_flags=%s",
        {
            "has_storage_url": settings.has_storage(),
            "has_abad_client": settings.has_abad_client(),
            "has_drive": settings.has_drive(),
            "has_auth": settings.has_auth(),
        },
    )
    if not settings.has_auth():
        log.warning("VERIFICADOR_API_KEY no configurado — todos los requests POST/GET con auth fallarán.")
    if not settings.has_storage():
        log.info("DATABASE_URL no configurado — usando SQLite local en outputs/verificador.db.")

    # Inicializar la BD del verificador (idempotente)
    from . import storage as _storage
    _storage.init_db()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Probe simple. No toca BDs ni servicios externos."""
    return HealthResponse(
        config={
            "storage": settings.has_storage(),
            "abad_client": settings.has_abad_client(),
            "drive": settings.has_drive(),
            "auth": settings.has_auth(),
        }
    )
