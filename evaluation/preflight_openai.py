from __future__ import annotations

import os
import sys
import time
from typing import Optional

from dotenv import load_dotenv


def _build_client() -> object:
    from openai import OpenAI

    return OpenAI(timeout=10, max_retries=0)


def _call_minimal(client: object, model: str) -> None:
    if hasattr(client, "responses"):
        client.responses.create(model=model, input="ping", max_output_tokens=16)
        return
    if hasattr(client, "chat") and hasattr(client.chat, "completions"):
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=16,
        )
        return
    raise RuntimeError("El SDK de OpenAI no expone responses ni chat.completions.")


def run_preflight() -> int:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI PREFLIGHT FAIL")
        print("Error: OPENAI_API_KEY no está configurada.")
        return 1

    model = os.getenv("EVAL_MODEL") or os.getenv("DEEPEVAL_MODEL") or "gpt-4o-mini"
    try:
        client = _build_client()
        start = time.perf_counter()
        _call_minimal(client, model)
        dur = (time.perf_counter() - start) * 1000
        print("OPENAI PREFLIGHT OK")
        print(f"latencia_ms={dur:.2f}")
        return 0
    except Exception as exc:
        print("OPENAI PREFLIGHT FAIL")
        print(f"Error: {exc}")
        return 1


def main() -> int:
    return run_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
