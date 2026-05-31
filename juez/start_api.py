#!/usr/bin/env python3
"""start_api.py — Arranca el servidor FastAPI del Juez.

Uso:
    python start_api.py                            # host=0.0.0.0, port=8765
    python start_api.py --port 9000                # cambiar puerto
    python start_api.py --host 127.0.0.1           # solo localhost
    python start_api.py --reload                   # auto-reload en cambios (dev)

Variables de entorno relevantes:
    JUEZ_API_HOST         host (default: 0.0.0.0)
    JUEZ_API_PORT         puerto (default: 8765)
    OPENAI_API_KEY        para análisis con GPT y contra-agente
    ELEVENLABS_API_KEY    para descargar agentes ElevenLabs
    N8N_API_KEY           para descargar flujos n8n por ID
    N8N_BASE_URL          URL base de tu instancia n8n
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="start_api.py", add_help=True)
    parser.add_argument(
        "--host", default=os.getenv("JUEZ_API_HOST", "0.0.0.0"),
        help="Host de escucha (default: 0.0.0.0 — env: JUEZ_API_HOST)",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("JUEZ_API_PORT", "8765")),
        help="Puerto de escucha (default: 8765 — env: JUEZ_API_PORT)",
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="Auto-reload en cambios (solo para desarrollo)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Número de workers de uvicorn (default: 1; los jobs ya usan threads)",
    )
    args = parser.parse_args()

    # Asegurar que el root del proyecto esté en sys.path
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn no está instalado. Instala con: pip install uvicorn[standard]")
        sys.exit(1)

    print("=" * 70)
    print("  LAMBDA ANALYTICS JUEZ — API Server")
    print("=" * 70)
    print(f"  Host          : {args.host}")
    print(f"  Puerto        : {args.port}")
    print(f"  Reload        : {args.reload}")
    print(f"  Workers       : {args.workers}")
    print(f"  Docs (Swagger): http://localhost:{args.port}/docs")
    print(f"  Health        : http://localhost:{args.port}/api/v1/health")
    print("=" * 70)

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
