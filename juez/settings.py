import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "AI Eval Service")
    ENV: str = os.getenv("ENV", "dev")

    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    LANGWATCH_API_KEY: str | None = os.getenv("LANGWATCH_API_KEY")

    JUDGE_MODEL: str = os.getenv("JUDGE_MODEL", "gpt-4o-mini")

    ENABLE_LANGWATCH: bool = os.getenv("ENABLE_LANGWATCH", "true").lower() == "true"

    # ── Modo e2e sintético: conexión al Verificador (otro servicio del repo) ──
    # El Juez puede invocar al Verificador para auditar un PDF sintético en
    # 1 de N casos. Solo se usa si el CLI se invoca con --e2e.
    VERIFICADOR_BASE_URL: str = os.getenv("VERIFICADOR_BASE_URL", "http://localhost:8001")
    VERIFICADOR_API_KEY: str | None = os.getenv("VERIFICADOR_API_KEY")
    # Modelo que el MockAgent usa para simular al agente bajo test. Default
    # gpt-4o-mini por costo. Override con env si quieren usar el mismo modelo
    # del agente real (p.ej. gpt-5.4) cuando esté disponible.
    JUEZ_E2E_MODEL: str = os.getenv("JUEZ_E2E_MODEL", "gpt-4o-mini")
    # Si el verificador no responde a healthcheck en este timeout, se degrada
    # a e2e_k=0 y el batch sigue normal.
    JUEZ_E2E_HEALTH_TIMEOUT_S: float = float(os.getenv("JUEZ_E2E_HEALTH_TIMEOUT_S", "2"))


settings = Settings()