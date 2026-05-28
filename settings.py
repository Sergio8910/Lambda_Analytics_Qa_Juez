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


settings = Settings()