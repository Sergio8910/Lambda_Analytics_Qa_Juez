"""Configuración del Verificador.

Patrón: os.getenv + python-dotenv (mismo patrón que el `settings.py` raíz
del Juez para no introducir nueva dependencia `pydantic-settings`).

CRÍTICO: ninguna credencial vive en código. Todas vienen de `.env` que
está gitignored. Si un valor sensible aparece en logs, es un bug.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Carga el .env del root del proyecto (un solo lugar de truth)
load_dotenv()


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings:
    # ── BD propia del verificador ────────────────────────────────────────────
    # Reusa la misma instancia Postgres del Juez, pero el schema "verificador"
    # aísla las tablas. Si se quiere separar instancias, basta cambiar esta
    # URL en .env (no hace falta tocar código).
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")
    DB_SCHEMA: str = "verificador"

    # ── BD del cliente Abad (READ-ONLY) ──────────────────────────────────────
    # Conexión con statement_timeout corto y SET TRANSACTION READ ONLY al
    # abrir. Idealmente apunta a un usuario `verificador_ro` con SELECT-only.
    ABAT_DB_URL: str | None = os.getenv("ABAT_DB_URL")

    # ── Google Drive ─────────────────────────────────────────────────────────
    GOOGLE_OAUTH_TOKEN: str | None = os.getenv("GOOGLE_OAUTH_TOKEN")

    # ── Endpoint del verificador ─────────────────────────────────────────────
    # API key requerida en header X-Verifier-Key. Sin default — si falta, el
    # servicio arranca pero rechaza todos los requests.
    VERIFICADOR_API_KEY: str | None = os.getenv("VERIFICADOR_API_KEY")
    VERIFICADOR_PORT: int = _get_int("VERIFICADOR_PORT", 8001)

    # ── Comportamiento ───────────────────────────────────────────────────────
    DRIVE_TIMEOUT_S: int = _get_int("VERIFICADOR_DRIVE_TIMEOUT_S", 60)
    DRIVE_RETRY_DELAY_S: int = _get_int("VERIFICADOR_DRIVE_RETRY_DELAY_S", 3)
    DRIVE_RETRY_MAX: int = _get_int("VERIFICADOR_DRIVE_RETRY_MAX", 3)
    CLIENT_DB_TIMEOUT_MS: int = _get_int("VERIFICADOR_CLIENT_DB_TIMEOUT_MS", 5000)

    # Cap de tamaño descarga (50MB). Mayor → UNVERIFIABLE.
    MAX_ARTIFACT_BYTES: int = _get_int("VERIFICADOR_MAX_ARTIFACT_BYTES", 50 * 1024 * 1024)

    LOG_LEVEL: str = os.getenv("VERIFICADOR_LOG_LEVEL", "INFO")

    # ── Sanity flags ─────────────────────────────────────────────────────────
    @classmethod
    def has_storage(cls) -> bool:
        return bool(cls.DATABASE_URL)

    @classmethod
    def has_abad_client(cls) -> bool:
        return bool(cls.ABAT_DB_URL)

    @classmethod
    def has_drive(cls) -> bool:
        return bool(cls.GOOGLE_OAUTH_TOKEN)

    @classmethod
    def has_auth(cls) -> bool:
        return bool(cls.VERIFICADOR_API_KEY)


settings = Settings()
