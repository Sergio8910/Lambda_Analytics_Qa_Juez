"""Configuración de Prompt Eval. Solo lo que este producto necesita."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Settings auto-suficientes. NO importa nada del Juez ni del Verificador."""

    APP_NAME: str = os.getenv("PROMPT_EVAL_APP_NAME", "Prompt Eval")
    ENV: str = os.getenv("ENV", "dev")
    PORT: int = int(os.getenv("PROMPT_EVAL_PORT", "8002"))

    # API key para autenticar requests al endpoint. Si vacía, no se exige header.
    API_KEY: str | None = os.getenv("PROMPT_EVAL_API_KEY")

    # LLM judge opcional. Si no hay key, el judge se omite y se devuelven solo
    # los findings determinísticos.
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    JUDGE_MODEL: str = os.getenv("PROMPT_EVAL_JUDGE_MODEL", os.getenv("JUDGE_MODEL", "gpt-4o-mini"))
    JUDGE_TIMEOUT_S: float = float(os.getenv("PROMPT_EVAL_JUDGE_TIMEOUT_S", "30"))

    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "text")
    LOG_LEVEL: str = os.getenv("PROMPT_EVAL_LOG_LEVEL", "INFO")


settings = Settings()
